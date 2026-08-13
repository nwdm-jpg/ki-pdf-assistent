from datetime import datetime

import streamlit as st
from pypdf import PdfReader

import analyse
import komponenten
import pdf_verarbeitung
import pruefung
import retrieval
import speicher
from pdf_logik import (
    formatiere_quellenhinweis,
    frage_beantworten,
    relevanten_text_zusammenstellen,
    verwendete_quellen,
)


BEREICH_START = "🏠 Startseite"
BEREICH_CHAT = "💬 Chat"
BEREICH_ANALYSE = "🔍 Analyse & Vergleich"
BEREICH_PRUEFUNG = "🛡️ Dokument prüfen"
BEREICHE = [BEREICH_START, BEREICH_CHAT, BEREICH_ANALYSE, BEREICH_PRUEFUNG]


st.set_page_config(
    page_title="KI-PDF-Assistent",
    page_icon="📄",
    layout="wide",
)

komponenten.css_einbinden()


speicher.datenbank_initialisieren()


if "aktueller_chat_id" not in st.session_state:
    vorhandene_chats = speicher.chat_liste()
    st.session_state.aktueller_chat_id = (
        vorhandene_chats[0]["id"] if vorhandene_chats else speicher.chat_erstellen()
    )

if "aktiver_bereich" not in st.session_state:
    st.session_state.aktiver_bereich = BEREICH_START

# Ermöglicht Navigation per Button-Klick (z. B. die großen Startseiten-
# Karten): Der Bereichswechsel wird über einen separaten, nicht an ein
# Widget gebundenen Key angefordert und HIER - vor der Instanziierung
# des Sidebar-Radios weiter unten - angewendet. Ein direktes Setzen von
# st.session_state.aktiver_bereich aus dem Hauptbereich heraus würde
# scheitern, da das Radio-Widget mit diesem Key in diesem Lauf bereits
# vorher gerendert worden wäre.
if "_bereich_wechsel" in st.session_state:
    st.session_state.aktiver_bereich = st.session_state.pop("_bereich_wechsel")


def dateien_verarbeiten(dateien):
    """Verarbeitet hochgeladene PDF-Dateien: Chunking, Embeddings, Speichern.

    Gemeinsam genutzt vom Sidebar-Uploader und dem Startseiten-Uploader,
    damit es nur einen Verarbeitungsweg (und keine zweite
    Speicherimplementierung) gibt.
    """
    for datei in dateien or []:
        pdf_bytes = datei.getvalue()
        hash_wert = speicher.hash_berechnen(pdf_bytes)

        if speicher.dokument_nach_hash(hash_wert):
            continue

        try:
            reader = PdfReader(datei)
        except Exception as fehler:
            st.error(f"„{datei.name}“ konnte nicht gelesen werden.")
            st.caption(f"Technische Details: {fehler}")
            continue

        rohe_chunks = pdf_verarbeitung.dokument_chunks_erstellen(reader, datei.name)

        if not rohe_chunks:
            st.warning(f"„{datei.name}“ enthält keinen extrahierbaren Text.")
            continue

        try:
            with st.spinner(f"Verarbeite „{datei.name}“..."):
                embeddings = retrieval.embeddings_batch_erstellen(
                    [chunk["text"] for chunk in rohe_chunks]
                )
                dokument_id = speicher.dokument_speichern(
                    datei.name, hash_wert, pdf_bytes, len(reader.pages)
                )
                speicher.chunks_speichern(dokument_id, rohe_chunks, embeddings)
        except Exception as fehler:
            st.error(f"„{datei.name}“ konnte nicht verarbeitet werden.")
            st.caption(f"Technische Details: {fehler}")
            continue

        st.success(f"„{datei.name}“ zur Bibliothek hinzugefügt.")


def _pruefung_starten(modus, icon, titel, funktion, dokument_ids):
    """Führt eine Prüfkategorie (oder den kompletten Check) aus und speichert das Ergebnis."""
    try:
        with st.spinner(f"{titel} wird erstellt..."):
            daten = funktion()

        st.session_state.pruefung_ergebnis = {
            "modus": modus,
            "icon": icon,
            "titel": titel,
            "dokument_ids": list(dokument_ids),
            "daten": daten,
            "rueckfragen": [],
        }
    except Exception as fehler:
        st.session_state.pruefung_ergebnis = None
        st.error(f"{titel} ist fehlgeschlagen.")
        st.caption(f"Technische Details: {fehler}")


with st.sidebar:
    st.markdown("## 📄 KI-PDF-Assistent")

    bereich = st.radio(
        "Bereich",
        BEREICHE,
        key="aktiver_bereich",
        label_visibility="collapsed",
    )

    st.divider()

    if bereich == BEREICH_CHAT:
        if st.button("＋ Neuer Chat", use_container_width=True):
            st.session_state.aktueller_chat_id = speicher.chat_erstellen()
            st.rerun()

        st.markdown("#### Chats")

        chats = speicher.chat_liste()

        for chat in chats:
            ist_aktiv = chat["id"] == st.session_state.aktueller_chat_id
            spalte_titel, spalte_loeschen = st.columns([5, 1])

            if spalte_titel.button(
                chat["titel"],
                key=f"chat_{chat['id']}",
                use_container_width=True,
                type="primary" if ist_aktiv else "secondary",
            ):
                st.session_state.aktueller_chat_id = chat["id"]
                st.rerun()

            if spalte_loeschen.button("🗑", key=f"del_chat_{chat['id']}"):
                speicher.chat_loeschen(chat["id"])

                if ist_aktiv:
                    uebrige_chats = [c for c in chats if c["id"] != chat["id"]]
                    st.session_state.aktueller_chat_id = (
                        uebrige_chats[0]["id"]
                        if uebrige_chats
                        else speicher.chat_erstellen()
                    )

                st.rerun()

        st.divider()

    st.markdown("#### 📤 Dokumente hinzufügen")
    st.caption("PDFs werden dauerhaft in der Bibliothek gespeichert.")

    hochgeladene_dateien = st.file_uploader(
        "PDF-Dateien auswählen",
        type=["pdf"],
        accept_multiple_files=True,
        key="sidebar_uploader",
    )
    dateien_verarbeiten(hochgeladene_dateien)

    st.divider()
    st.markdown("#### 📚 Dokumentenbibliothek")

    dokumente = speicher.dokumente_laden()

    # Im Chat-Bereich bestimmt der aktuelle Chat, welche Checkboxen als
    # ausgewählt gelten. Die Checkbox-Keys sind bewusst pro Chat UND
    # Dokument vergeben (statt nur pro Dokument), damit Streamlits
    # eigener Widget-State beim Chatwechsel nicht die Auswahl eines
    # anderen Chats "durchschlagen" lässt. In allen anderen Bereichen ist
    # die Dokumentauswahl bewusst unabhängig davon (siehe Hauptbereich)
    # und wird hier nur informativ, ohne Checkboxen, aufgelistet.
    chat_bereich = bereich == BEREICH_CHAT

    if chat_bereich:
        aktueller_chat_id = st.session_state.aktueller_chat_id
        aktueller_chat = speicher.chat_laden(aktueller_chat_id)
        aktive_dokument_ids = set(aktueller_chat["dokument_ids"])

    if not dokumente:
        komponenten.leerer_zustand("Noch keine Dokumente in der Bibliothek.")
    else:
        if chat_bereich:
            st.caption(
                f"{len(aktive_dokument_ids)} von {len(dokumente)} Dokumenten "
                "für diesen Chat ausgewählt."
            )
        else:
            wort = "Dokument" if len(dokumente) == 1 else "Dokumente"
            st.caption(f"{len(dokumente)} {wort} in der Bibliothek.")

        suchbegriff = ""
        if len(dokumente) > 5:
            suchbegriff = st.text_input(
                "Dokumente durchsuchen",
                placeholder="🔍 Dokument suchen...",
                label_visibility="collapsed",
            ).strip().lower()

        angezeigte_dokumente = [
            dokument
            for dokument in dokumente
            if suchbegriff in dokument["dateiname"].lower()
        ]

        if suchbegriff and not angezeigte_dokumente:
            st.caption("Keine Dokumente gefunden.")

        for dokument in angezeigte_dokumente:
            dokument_id = dokument["id"]

            with st.container(border=True):
                seiten_wort = "Seite" if dokument["seitenzahl"] == 1 else "Seiten"
                hochgeladen_am = datetime.fromisoformat(
                    dokument["hochgeladen_am"]
                ).strftime("%d.%m.%Y")

                if chat_bereich:
                    checkbox_key = f"aktiv_{aktueller_chat_id}_{dokument_id}"
                    ausgewaehlt = st.checkbox(
                        f"**{dokument['dateiname']}**",
                        value=dokument_id in aktive_dokument_ids,
                        key=checkbox_key,
                    )
                    status = (
                        "✅ Aktiv in diesem Chat"
                        if ausgewaehlt
                        else "Nicht ausgewählt"
                    )
                else:
                    st.markdown(f"**{dokument['dateiname']}**")
                    status = None

                meta_text = f"{dokument['seitenzahl']} {seiten_wort} · hochgeladen am {hochgeladen_am}"
                if status:
                    meta_text = f"{status} · {meta_text}"

                spalte_meta, spalte_loeschen = st.columns([5, 1])
                spalte_meta.caption(meta_text)

                with spalte_loeschen.popover("🗑"):
                    st.write(f"„{dokument['dateiname']}“ entfernen?")
                    st.caption(
                        "Löscht auch alle gespeicherten Textausschnitte und "
                        "entfernt das Dokument aus allen Chats."
                    )
                    if st.button(
                        "Endgültig löschen",
                        key=f"confirm_del_{dokument_id}",
                        type="primary",
                        use_container_width=True,
                    ):
                        speicher.dokument_loeschen(dokument_id)
                        st.rerun()

        if chat_bereich:
            # Auswahl über ALLE Dokumente auswerten (nicht nur die
            # aktuell sichtbaren), damit ein aktiver Suchfilter die
            # Auswahl gerade ausgeblendeter Dokumente nicht verwirft.
            neue_aktive_ids = {
                dokument["id"]
                for dokument in dokumente
                if st.session_state.get(
                    f"aktiv_{aktueller_chat_id}_{dokument['id']}",
                    dokument["id"] in aktive_dokument_ids,
                )
            }

            if neue_aktive_ids != aktive_dokument_ids:
                speicher.chat_dokumente_setzen(aktueller_chat_id, sorted(neue_aktive_ids))
                st.rerun()


if bereich == BEREICH_START:
    st.title("Willkommen")
    st.caption("Was möchtest du mit deinen Dokumenten machen?")

    spalte_chat, spalte_analyse, spalte_pruefung = st.columns(3)

    with spalte_chat:
        if komponenten.start_karte(
            "💬",
            "Mit Dokumenten chatten",
            "Stelle Fragen und erhalte Antworten direkt aus deinen Dokumenten.",
            "Chat starten",
            key="chat",
        ):
            st.session_state["_bereich_wechsel"] = BEREICH_CHAT
            st.rerun()

    with spalte_analyse:
        if komponenten.start_karte(
            "🔍",
            "Analyse & Vergleich",
            "Fasse Dokumente zusammen, vergleiche Inhalte und finde wichtige Fristen.",
            "Analyse starten",
            key="analyse",
        ):
            st.session_state["_bereich_wechsel"] = BEREICH_ANALYSE
            st.rerun()

    with spalte_pruefung:
        if komponenten.start_karte(
            "🛡️",
            "Dokument prüfen",
            "Lass wichtige Stellen, Risiken, Pflichten und Auffälligkeiten automatisch prüfen.",
            "Dokument prüfen",
            key="pruefung",
        ):
            st.session_state["_bereich_wechsel"] = BEREICH_PRUEFUNG
            st.rerun()

    st.divider()

    alle_dokumente_start = speicher.dokumente_laden()

    if alle_dokumente_start:
        wort = "Dokument" if len(alle_dokumente_start) == 1 else "Dokumente"
        st.markdown(f"#### 📚 {len(alle_dokumente_start)} {wort} in deiner Bibliothek")

        neueste = alle_dokumente_start[:3]
        spalten = st.columns(len(neueste))

        for spalte, dokument in zip(spalten, neueste):
            with spalte:
                with st.container(border=True):
                    st.markdown(f"**{dokument['dateiname']}**")
                    seiten_wort = "Seite" if dokument["seitenzahl"] == 1 else "Seiten"
                    st.caption(f"{dokument['seitenzahl']} {seiten_wort}")
    else:
        komponenten.leerer_zustand("Füge zuerst ein PDF zu deiner Dokumentenbibliothek hinzu.")

    st.markdown("##### 📤 Dokument hinzufügen")
    home_dateien = st.file_uploader(
        "PDF-Dateien auswählen",
        type=["pdf"],
        accept_multiple_files=True,
        key="home_uploader",
        label_visibility="collapsed",
    )
    dateien_verarbeiten(home_dateien)


elif bereich == BEREICH_CHAT:
    aktueller_chat = speicher.chat_laden(st.session_state.aktueller_chat_id)
    alle_dokumente = {dokument["id"]: dokument for dokument in speicher.dokumente_laden()}
    aktive_dokumente = [
        alle_dokumente[i] for i in aktueller_chat["dokument_ids"] if i in alle_dokumente
    ]

    komponenten.seiten_kopf(aktueller_chat["titel"])

    if not alle_dokumente:
        komponenten.leerer_zustand("Füge zuerst ein PDF zu deiner Dokumentenbibliothek hinzu.")
    elif not aktive_dokumente:
        komponenten.leerer_zustand("Wähle Dokumente aus und stelle deine erste Frage.")
    else:
        aktive_namen = ", ".join(dokument["dateiname"] for dokument in aktive_dokumente)
        st.caption(f"Aktive Dokumente: {aktive_namen}")

        for nachricht in aktueller_chat["nachrichten"]:
            with st.chat_message("user"):
                st.write(nachricht["frage"])

            with st.chat_message("assistant"):
                st.write(nachricht["antwort"])
                komponenten.quellen_hinweis(formatiere_quellenhinweis(nachricht["quellen"]))

        frage = st.chat_input("Stelle eine Frage zu deinen Dokumenten...")

        if frage:
            with st.chat_message("user"):
                st.write(frage)

            vorherige_nachrichten = aktueller_chat["nachrichten"]

            # Die letzten Chatrunden fließen zusätzlich in die Chunk-Suche
            # ein, damit Rückfragen wie "Und wie ist das im zweiten Vertrag?"
            # noch die passenden Textstellen finden.
            zusatzkontext = "\n".join(
                f"{nachricht['frage']} {nachricht['antwort']}"
                for nachricht in vorherige_nachrichten[-2:]
            )

            # Pro Dokument mehrere Chunks zulassen, aber bei vielen Dokumenten
            # die Gesamtzahl der Auszüge im Prompt begrenzen. Jedes aktive
            # Dokument erhält so mindestens eine Chance, im Kontext zu
            # erscheinen.
            anzahl_dokumente = len(aktive_dokumente)
            anzahl_pro_dokument = (
                4 if anzahl_dokumente == 1 else max(2, 8 // anzahl_dokumente)
            )

            try:
                with st.spinner("Durchsuche deine Dokumente..."):
                    alle_chunks = speicher.chunks_laden(
                        [dokument["id"] for dokument in aktive_dokumente]
                    )
                    beste_chunks = retrieval.relevante_chunks_ermitteln(
                        frage,
                        alle_chunks,
                        anzahl_pro_dokument=anzahl_pro_dokument,
                        zusatzkontext=zusatzkontext,
                    )

                relevanter_text = relevanten_text_zusammenstellen(beste_chunks)
                ausgewaehlte_quellen = verwendete_quellen(beste_chunks)

                with st.chat_message("assistant"):
                    with st.spinner("Die KI beantwortet deine Frage..."):
                        antwort_text = frage_beantworten(
                            frage,
                            relevanter_text,
                            verlauf=vorherige_nachrichten[-6:],
                        )

                    st.write(antwort_text)
                    komponenten.quellen_hinweis(formatiere_quellenhinweis(ausgewaehlte_quellen))

                speicher.nachricht_hinzufuegen(
                    st.session_state.aktueller_chat_id,
                    frage,
                    antwort_text,
                    ausgewaehlte_quellen,
                )

                st.rerun()

            except Exception as fehler:
                st.error("Die KI-Anfrage ist fehlgeschlagen.")
                st.caption(f"Technische Details: {fehler}")


elif bereich == BEREICH_ANALYSE:
    komponenten.seiten_kopf(
        BEREICH_ANALYSE,
        "Fasse Dokumente zusammen, vergleiche Inhalte und finde wichtige Fristen.",
    )

    alle_dokumente_liste = speicher.dokumente_laden()

    if not alle_dokumente_liste:
        komponenten.leerer_zustand("Füge zuerst ein PDF zu deiner Dokumentenbibliothek hinzu.")
    else:
        namen_je_id = {d["id"]: d["dateiname"] for d in alle_dokumente_liste}

        ausgewaehlte_ids = komponenten.dokument_mehrfachauswahl(
            "Dokumente für die Analyse auswählen",
            session_key="analyse_dokument_ids",
            widget_key="analyse_dokument_ids_widget",
            dokumente=alle_dokumente_liste,
            hilfetext=(
                "Diese Auswahl ist unabhängig von der Dokumentauswahl "
                "einzelner Chats und der Dokumentprüfung."
            ),
        )

        st.divider()

        AKTIONEN = [
            {
                "modus": "zusammenfassen",
                "icon": "📋",
                "titel": "Zusammenfassen",
                "beschreibung": "Strukturierte Zusammenfassung je Dokument.",
                "mindestens": 1,
                "funktion": analyse.zusammenfassen,
            },
            {
                "modus": "vergleichen",
                "icon": "⚖️",
                "titel": "Dokumente vergleichen",
                "beschreibung": "Vergleichstabelle + wichtigste Unterschiede.",
                "mindestens": 2,
                "funktion": analyse.vergleichen,
            },
            {
                "modus": "fristen",
                "icon": "📅",
                "titel": "Fristen & Termine",
                "beschreibung": "Daten, Fristen und Termine, chronologisch.",
                "mindestens": 1,
                "funktion": analyse.fristen_ermitteln,
            },
            {
                "modus": "risiken",
                "icon": "⚠️",
                "titel": "Risiken & Auffälligkeiten",
                "beschreibung": "Auffällige oder wichtige Klauseln im Blick.",
                "mindestens": 1,
                "funktion": analyse.risiken_ermitteln,
            },
        ]

        spalten = st.columns(4)

        if "analyse_ergebnis" not in st.session_state:
            st.session_state.analyse_ergebnis = None

        for spalte, aktion in zip(spalten, AKTIONEN):
            with spalte:
                zu_wenige = len(ausgewaehlte_ids) < aktion["mindestens"]
                hinweis = None

                if zu_wenige:
                    hinweis = (
                        f"Benötigt mindestens {aktion['mindestens']} "
                        f"Dokument{'e' if aktion['mindestens'] > 1 else ''}."
                    )

                if komponenten.modus_karte(
                    aktion["icon"],
                    aktion["titel"],
                    aktion["beschreibung"],
                    "Ausführen",
                    key=f"analyse_start_{aktion['modus']}",
                    deaktiviert=zu_wenige,
                    deaktiviert_hinweis=hinweis,
                ):
                    try:
                        with st.spinner(f"{aktion['titel']} wird erstellt..."):
                            daten = aktion["funktion"](ausgewaehlte_ids)

                        st.session_state.analyse_ergebnis = {
                            "modus": aktion["modus"],
                            "titel": aktion["titel"],
                            "icon": aktion["icon"],
                            "dokument_ids": list(ausgewaehlte_ids),
                            "daten": daten,
                            "rueckfragen": [],
                        }
                    except Exception as fehler:
                        st.session_state.analyse_ergebnis = None
                        st.error(f"{aktion['titel']} ist fehlgeschlagen.")
                        st.caption(f"Technische Details: {fehler}")

        st.divider()

        ergebnis = st.session_state.analyse_ergebnis

        if not ergebnis:
            komponenten.leerer_zustand("Wähle Dokumente und starte eine Analyse.")
        else:
            dokument_namen = ", ".join(
                namen_je_id.get(i, "(gelöscht)") for i in ergebnis["dokument_ids"]
            )

            if komponenten.ergebnis_kopf(
                ergebnis["icon"], ergebnis["titel"], dokument_namen,
                reset_key="analyse_ergebnis_leeren",
            ):
                st.session_state.analyse_ergebnis = None
                st.rerun()

            if ergebnis["modus"] == "risiken":
                st.warning(analyse.RISIKEN_HINWEIS)

            with st.container(border=True):
                st.markdown(ergebnis["daten"]["text"])

            komponenten.quellen_hinweis(ergebnis["daten"]["quellenhinweis"])

            komponenten.rueckfragen_chat(
                ergebnis,
                "analyse_ergebnis",
                lambda erg, frage: analyse.rueckfrage_beantworten(
                    erg["daten"]["text"], erg["dokument_ids"], frage, verlauf=erg["rueckfragen"]
                ),
                "Frage zur Analyse stellen...",
                "💬 Rückfragen zur Analyse",
            )


else:  # BEREICH_PRUEFUNG
    komponenten.seiten_kopf(
        BEREICH_PRUEFUNG,
        "Lass wichtige Stellen, Risiken, Pflichten und Fristen automatisch prüfen.",
    )

    alle_dokumente_liste = speicher.dokumente_laden()

    if not alle_dokumente_liste:
        komponenten.leerer_zustand("Füge zuerst ein PDF zu deiner Dokumentenbibliothek hinzu.")
    else:
        namen_je_id = {d["id"]: d["dateiname"] for d in alle_dokumente_liste}

        spalte_docs, spalte_preset = st.columns([3, 2])

        with spalte_docs:
            ausgewaehlte_ids = komponenten.dokument_mehrfachauswahl(
                "Dokumente für die Prüfung auswählen",
                session_key="pruefung_dokument_ids",
                widget_key="pruefung_dokument_ids_widget",
                dokumente=alle_dokumente_liste,
                hilfetext=(
                    "Diese Auswahl ist unabhängig von der Dokumentauswahl "
                    "einzelner Chats und der Analyse."
                ),
            )

        with spalte_preset:
            preset_id = st.selectbox(
                "Prüfvorlage",
                options=list(pruefung.PRESETS.keys()),
                format_func=lambda k: pruefung.PRESETS[k]["titel"],
                key="pruefung_preset",
            )

        st.divider()

        if "pruefung_ergebnis" not in st.session_state:
            st.session_state.pruefung_ergebnis = None

        zu_wenige_komplett = not ausgewaehlte_ids

        if st.button(
            "🛡️ Kompletten Dokumenten-Check starten",
            key="pruefung_start_komplett",
            use_container_width=True,
            type="primary",
            disabled=zu_wenige_komplett,
        ):
            _pruefung_starten(
                "komplett",
                "🛡️",
                "Kompletter Dokumenten-Check",
                lambda: pruefung.kompletter_check(ausgewaehlte_ids, preset_id),
                ausgewaehlte_ids,
            )

        if zu_wenige_komplett:
            st.caption("Wähle mindestens ein Dokument aus, um eine Prüfung zu starten.")

        st.markdown("###### Einzelne Prüfkategorien")

        kategorie_spalten = st.columns(3)

        for index, kategorie_id in enumerate(pruefung.KATEGORIEN):
            kategorie = pruefung.KATEGORIEN[kategorie_id]

            with kategorie_spalten[index % 3]:
                if komponenten.modus_karte(
                    kategorie["icon"],
                    kategorie["titel"],
                    kategorie["beschreibung"],
                    "Prüfen",
                    key=f"pruefung_start_{kategorie_id}",
                    deaktiviert=not ausgewaehlte_ids,
                ):
                    _pruefung_starten(
                        kategorie_id,
                        kategorie["icon"],
                        kategorie["titel"],
                        lambda kid=kategorie_id: pruefung.einzelpruefung(
                            kid, ausgewaehlte_ids, preset_id
                        ),
                        ausgewaehlte_ids,
                    )

        st.divider()

        ergebnis = st.session_state.pruefung_ergebnis

        if not ergebnis:
            komponenten.leerer_zustand("Wähle ein Dokument und starte eine Prüfung.")
        else:
            dokument_namen = ", ".join(
                namen_je_id.get(i, "(gelöscht)") for i in ergebnis["dokument_ids"]
            )

            if komponenten.ergebnis_kopf(
                ergebnis["icon"], ergebnis["titel"], dokument_namen,
                reset_key="pruefung_ergebnis_leeren",
            ):
                st.session_state.pruefung_ergebnis = None
                st.rerun()

            st.warning(pruefung.PRUEFUNG_HINWEIS)
            st.caption(pruefung.PRIORITAETS_LEGENDE)

            with st.container(border=True):
                st.markdown(ergebnis["daten"]["text"])

            komponenten.quellen_hinweis(ergebnis["daten"]["quellenhinweis"])

            komponenten.rueckfragen_chat(
                ergebnis,
                "pruefung_ergebnis",
                lambda erg, frage: pruefung.rueckfrage_beantworten(
                    erg["daten"]["text"], erg["dokument_ids"], frage, verlauf=erg["rueckfragen"]
                ),
                "Frage zur Prüfung stellen...",
                "💬 Rückfragen zur Prüfung",
            )
