from datetime import datetime, timedelta

import streamlit as st

import analyse
import dokument_verarbeitung
import dokumentbibliothek
import komponenten
import pruefung
import retrieval
import speicher
from pdf_logik import frage_beantworten
from quellen import (
    formatiere_quellenhinweis,
    relevanten_text_zusammenstellen,
    verwendete_quellen,
)


BEREICH_START = "🏠 Startseite"
BEREICH_CHAT = "💬 Chat"
BEREICH_ANALYSE = "🔍 Analyse & Vergleich"
BEREICH_PRUEFUNG = "🛡️ Dokument prüfen"
BEREICH_BIBLIOTHEK = "📚 Dokumentenbibliothek"


st.set_page_config(
    page_title="AVENLOQ",
    page_icon="🟣",
    layout="wide",
)

komponenten.css_einbinden()


speicher.datenbank_initialisieren()


if "aktueller_chat_id" not in st.session_state:
    vorhandene_chats = speicher.chat_liste()
    st.session_state.aktueller_chat_id = (
        vorhandene_chats[0]["id"] if vorhandene_chats else speicher.chat_erstellen()
    )

# Bewusst eine reine Session-State-Variable (nicht an ein einzelnes
# Widget gebunden): Die Navigation besteht aus mehreren Buttons
# (Sidebar-Haupt-Navigation UND die großen Startseiten-Karten), die alle
# denselben Zustand setzen können sollen. Ein Widget-gebundener Key
# (z. B. bei st.radio) ließe sich nach dessen Instanziierung im selben
# Lauf nicht mehr direkt setzen - als reine Variable ist das problemlos
# möglich, gefolgt von st.rerun().
if "aktiver_bereich" not in st.session_state:
    st.session_state.aktiver_bereich = BEREICH_START


def dateien_verarbeiten(dateien):
    """Verarbeitet hochgeladene Dateien: Text extrahieren, chunken, Embeddings, Speichern.

    Die einzige Verarbeitungs-/Speicherimplementierung der App - genutzt
    vom Uploader in der Dokumentenbibliothek (BEREICH_BIBLIOTHEK), damit
    es nur einen Verarbeitungsweg gibt. Die Formaterkennung (PDF, DOCX,
    TXT, MD, CSV, XLSX, PPTX) übernimmt `dokument_verarbeitung`; nicht
    unterstützte Dateitypen führen zu einer klaren deutschen
    Fehlermeldung statt eines Absturzes.
    """
    for datei in dateien or []:
        datei_bytes = datei.getvalue()
        hash_wert = speicher.hash_berechnen(datei_bytes)

        if speicher.dokument_nach_hash(hash_wert):
            continue

        try:
            chunks, einheiten_anzahl, einheit_typ, dateityp = (
                dokument_verarbeitung.dokument_verarbeiten(datei.name, datei_bytes)
            )
        except dokument_verarbeitung.NichtUnterstuetzterDateityp as fehler:
            st.error(str(fehler))
            continue
        except Exception as fehler:
            st.error(f"„{datei.name}“ konnte nicht gelesen werden.")
            st.caption(f"Technische Details: {fehler}")
            continue

        if not chunks:
            st.warning(f"„{datei.name}“ enthält keinen extrahierbaren Text.")
            continue

        try:
            with st.spinner(f"Verarbeite „{datei.name}“..."):
                embeddings = retrieval.embeddings_batch_erstellen(
                    [chunk["text"] for chunk in chunks]
                )
                dokument_id = speicher.dokument_speichern(
                    datei.name,
                    hash_wert,
                    datei_bytes,
                    einheiten_anzahl,
                    dateityp,
                    einheit_typ,
                )
                speicher.chunks_speichern(dokument_id, chunks, embeddings)
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
    komponenten.marke_kopf()

    bereich = st.session_state.aktiver_bereich

    # Startseite bewusst größer/prominenter als die übrigen Bereiche
    # (gleicher Button-Typ, nur über `gross=True` optisch hervorgehoben)
    # - sie ist der primäre Einstiegspunkt der App.
    if komponenten.nav_eintrag(BEREICH_START, aktiv=bereich == BEREICH_START, key="start", gross=True):
        st.session_state.aktiver_bereich = BEREICH_START
        st.rerun()

    for ziel, key_suffix in (
        (BEREICH_CHAT, "chat"),
        (BEREICH_ANALYSE, "analyse"),
        (BEREICH_PRUEFUNG, "pruefung"),
        (BEREICH_BIBLIOTHEK, "bibliothek"),
    ):
        if komponenten.nav_eintrag(ziel, aktiv=bereich == ziel, key=key_suffix):
            st.session_state.aktiver_bereich = ziel
            st.rerun()

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
        st.markdown("#### 📄 Dokumente in diesem Chat")

        aktueller_chat_id = st.session_state.aktueller_chat_id
        aktueller_chat = speicher.chat_laden(aktueller_chat_id)
        alle_dokumente_sidebar = speicher.dokumente_laden()

        if not alle_dokumente_sidebar:
            komponenten.leerer_zustand("Noch keine Dokumente in der Bibliothek.")
            st.caption("Füge Dokumente über „📚 Dokumentenbibliothek“ hinzu.")
        else:
            # Session-Key ist bewusst pro Chat vergeben (nicht global),
            # damit ein Chatwechsel im selben Lauf nicht die Auswahl
            # eines anderen Chats überschreibt - vorbereitet mit dem
            # gespeicherten Auswahlstand dieses Chats, bevor
            # `dokument_mehrfachauswahl` (siehe komponenten.py, bereits
            # von Analyse & Prüfung genutzt) sie initialisiert.
            session_key = f"chat_dokument_ids_{aktueller_chat_id}"

            if session_key not in st.session_state:
                st.session_state[session_key] = list(aktueller_chat["dokument_ids"])

            ausgewaehlte_ids = komponenten.dokument_mehrfachauswahl(
                "Aktive Dokumente",
                session_key=session_key,
                widget_key=f"chat_dokument_ids_widget_{aktueller_chat_id}",
                dokumente=alle_dokumente_sidebar,
                hilfetext="Nur ausgewählte Dokumente fließen in die Antworten dieses Chats ein.",
            )

            if set(ausgewaehlte_ids) != set(aktueller_chat["dokument_ids"]):
                speicher.chat_dokumente_setzen(aktueller_chat_id, ausgewaehlte_ids)
                st.rerun()


if bereich == BEREICH_START:
    komponenten.marke_kopf(gross=True)
    komponenten.marke_tagline()
    st.caption(
        "Nutze KI, um deine Dokumente schneller zu verstehen, zu analysieren "
        "und wichtige Informationen zu finden."
    )

    alle_dokumente_start = speicher.dokumente_laden()
    alle_chats_start = speicher.chat_liste()

    spalte_stat_dokumente, spalte_stat_chats = st.columns(2)
    spalte_stat_dokumente.metric("Dokumente", len(alle_dokumente_start))
    spalte_stat_chats.metric("Chats", len(alle_chats_start))

    st.divider()

    spalte_chat, spalte_analyse, spalte_pruefung, spalte_bibliothek = st.columns(4)

    with spalte_chat:
        if komponenten.start_karte(
            "💬",
            "Chat",
            "Stelle Fragen zu deinen Dokumenten.",
            "Chat starten",
            key="chat",
        ):
            st.session_state.aktiver_bereich = BEREICH_CHAT
            st.rerun()

    with spalte_analyse:
        if komponenten.start_karte(
            "🔍",
            "Analyse & Vergleich",
            "Fasse Inhalte zusammen und vergleiche Dokumente.",
            "Analyse starten",
            key="analyse",
        ):
            st.session_state.aktiver_bereich = BEREICH_ANALYSE
            st.rerun()

    with spalte_pruefung:
        if komponenten.start_karte(
            "🛡️",
            "Dokument prüfen",
            "Erkenne wichtige Punkte, Fristen und mögliche Risiken.",
            "Dokument prüfen",
            key="pruefung",
        ):
            st.session_state.aktiver_bereich = BEREICH_PRUEFUNG
            st.rerun()

    with spalte_bibliothek:
        if komponenten.start_karte(
            "📚",
            "Dokumentenbibliothek",
            "Verwalte und durchsuche deine Dokumente.",
            "Zur Bibliothek",
            key="bibliothek",
        ):
            st.session_state.aktiver_bereich = BEREICH_BIBLIOTHEK
            st.rerun()

    st.divider()

    if alle_dokumente_start:
        wort = "Dokument" if len(alle_dokumente_start) == 1 else "Dokumente"
        st.markdown(f"#### 📚 {len(alle_dokumente_start)} {wort} in deiner Bibliothek")

        neueste = alle_dokumente_start[:3]
        spalten = st.columns(len(neueste))

        for spalte, dokument in zip(spalten, neueste):
            with spalte:
                with st.container(border=True):
                    st.markdown(f"**{dokument['dateiname']}**")
                    st.caption(dokumentbibliothek.einheiten_text(dokument))
    else:
        komponenten.leerer_zustand("Füge zuerst ein Dokument zu deiner Dokumentenbibliothek hinzu.")


elif bereich == BEREICH_CHAT:
    aktueller_chat = speicher.chat_laden(st.session_state.aktueller_chat_id)
    alle_dokumente = {dokument["id"]: dokument for dokument in speicher.dokumente_laden()}
    aktive_dokumente = [
        alle_dokumente[i] for i in aktueller_chat["dokument_ids"] if i in alle_dokumente
    ]

    komponenten.seiten_kopf(aktueller_chat["titel"])

    if not alle_dokumente:
        komponenten.leerer_zustand("Füge zuerst ein Dokument zu deiner Dokumentenbibliothek hinzu.")
    elif not aktive_dokumente:
        komponenten.leerer_zustand("Wähle Dokumente aus und stelle deine erste Frage.")
    else:
        aktive_namen = ", ".join(dokument["dateiname"] for dokument in aktive_dokumente)
        st.caption(f"Aktive Dokumente: {aktive_namen}")

        if not aktueller_chat["nachrichten"]:
            komponenten.leerer_zustand("Stelle eine Frage zu deinen Dokumenten.")

        for nachricht in aktueller_chat["nachrichten"]:
            with st.chat_message("user"):
                st.write(nachricht["frage"])

            with st.chat_message("assistant"):
                st.write(nachricht["antwort"])
                komponenten.quellen_hinweis(formatiere_quellenhinweis(nachricht["quellen"]))

        frage = st.chat_input("Frage zu deinen Dokumenten …")

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
        komponenten.leerer_zustand("Füge zuerst ein Dokument zu deiner Dokumentenbibliothek hinzu.")
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
                komponenten.hinweis_dezent(analyse.RISIKEN_HINWEIS)

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


elif bereich == BEREICH_PRUEFUNG:
    komponenten.seiten_kopf(
        "Dokument prüfen",
        "Lass wichtige Stellen, Risiken, Pflichten und Fristen automatisch prüfen.",
    )

    alle_dokumente_liste = speicher.dokumente_laden()

    if not alle_dokumente_liste:
        komponenten.leerer_zustand("Füge zuerst ein Dokument zu deiner Dokumentenbibliothek hinzu.")
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

            komponenten.hinweis_dezent(pruefung.PRUEFUNG_HINWEIS)
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


else:  # BEREICH_BIBLIOTHEK
    komponenten.seiten_kopf(
        BEREICH_BIBLIOTHEK,
        "Alle deine Dokumente an einem Ort.",
    )

    st.markdown("## 📤 Dokumente hinzufügen")
    st.caption(
        "Unterstützte Formate: "
        + ", ".join(endung.upper() for endung in dokument_verarbeitung.SUPPORTED_EXTENSIONS)
    )

    bibliothek_dateien = st.file_uploader(
        "Dateien auswählen",
        type=dokument_verarbeitung.SUPPORTED_EXTENSIONS,
        accept_multiple_files=True,
        key="bibliothek_uploader",
        label_visibility="collapsed",
    )
    dateien_verarbeiten(bibliothek_dateien)

    st.divider()

    alle_dokumente_bibliothek = speicher.dokumente_laden()
    wort = "Dokument" if len(alle_dokumente_bibliothek) == 1 else "Dokumente"
    st.markdown(f"## 📚 {len(alle_dokumente_bibliothek)} {wort} in deiner Bibliothek")

    if not alle_dokumente_bibliothek:
        komponenten.leerer_zustand("Noch keine Dokumente in der Bibliothek. Lade oben deine erste Datei hoch.")
    else:
        spalte_suche, spalte_sortierung, spalte_typ = st.columns([3, 2, 2])

        suchbegriff = spalte_suche.text_input(
            "Dokumente durchsuchen",
            placeholder="🔍 Dokumente durchsuchen",
            label_visibility="collapsed",
            key="bibliothek_seite_suchbegriff",
        ).strip()

        sortierung = spalte_sortierung.selectbox(
            "Sortierung",
            options=dokumentbibliothek.SORTIERUNGEN,
            label_visibility="collapsed",
            key="bibliothek_seite_sortierung",
        )

        dateityp_filter = spalte_typ.selectbox(
            "Dateityp",
            options=dokumentbibliothek.dateitypen_optionen(alle_dokumente_bibliothek),
            format_func=lambda t: (
                t if t == dokumentbibliothek.DATEITYP_ALLE else dokumentbibliothek.dateityp_anzeige(t)
            ),
            label_visibility="collapsed",
            key="bibliothek_seite_dateityp",
        )

        spalte_datum, spalte_datumsbereich = st.columns([2, 3])

        datumsfilter = spalte_datum.selectbox(
            "Zeitraum",
            options=dokumentbibliothek.DATUMSFILTER,
            label_visibility="collapsed",
            key="bibliothek_seite_datumsfilter",
        )

        benutzerdefiniert_von = None
        benutzerdefiniert_bis = None

        if datumsfilter == dokumentbibliothek.DATUMSFILTER_BENUTZERDEFINIERT:
            heute = datetime.now().date()
            datumsbereich = spalte_datumsbereich.date_input(
                "Zeitraum wählen",
                value=(heute - timedelta(days=7), heute),
                label_visibility="collapsed",
                key="bibliothek_seite_datumsbereich",
            )
            if isinstance(datumsbereich, tuple) and len(datumsbereich) == 2:
                benutzerdefiniert_von, benutzerdefiniert_bis = datumsbereich

        gefilterte_dokumente = dokumentbibliothek.dokumente_filtern(
            alle_dokumente_bibliothek,
            suchbegriff=suchbegriff,
            datumsfilter=datumsfilter,
            benutzerdefiniert_von=benutzerdefiniert_von,
            benutzerdefiniert_bis=benutzerdefiniert_bis,
            dateityp_filter=dateityp_filter,
        )
        angezeigte_dokumente = dokumentbibliothek.dokumente_sortieren(
            gefilterte_dokumente, sortierung
        )

        gefiltert_aktiv = (
            suchbegriff
            or datumsfilter != dokumentbibliothek.DATUMSFILTER_ALLE
            or dateityp_filter != dokumentbibliothek.DATEITYP_ALLE
        )

        if gefiltert_aktiv:
            st.caption(
                f"{len(angezeigte_dokumente)} von {len(alle_dokumente_bibliothek)} "
                "Dokumenten angezeigt"
            )

        if not angezeigte_dokumente:
            komponenten.leerer_zustand("Keine Dokumente gefunden.")
        else:
            spalten = st.columns(2)

            for index, dokument in enumerate(angezeigte_dokumente):
                dokument_id = dokument["id"]
                hochgeladen_am = datetime.fromisoformat(
                    dokument["hochgeladen_am"]
                ).strftime("%d.%m.%Y")
                groesse = dokumentbibliothek.groesse_text(dokument)

                with spalten[index % 2].container(border=True):
                    st.markdown(f"**{dokument['dateiname']}**")
                    st.caption(
                        dokumentbibliothek.dateityp_anzeige(dokument.get("dateityp", "pdf"))
                        + " · "
                        + dokumentbibliothek.einheiten_text(dokument)
                    )

                    zusatz = f"Hochgeladen am {hochgeladen_am}"
                    if groesse:
                        zusatz += f" · {groesse}"
                    st.caption(zusatz)
                    st.caption("✅ Gespeichert")

                    with st.popover("🗑 Löschen"):
                        st.write(f"„{dokument['dateiname']}“ entfernen?")
                        st.caption(
                            "Löscht auch alle gespeicherten Textausschnitte und "
                            "entfernt das Dokument aus allen Chats."
                        )
                        if st.button(
                            "Endgültig löschen",
                            key=f"bibliothek_confirm_del_{dokument_id}",
                            type="secondary",
                            use_container_width=True,
                        ):
                            speicher.dokument_loeschen(dokument_id)
                            st.rerun()
