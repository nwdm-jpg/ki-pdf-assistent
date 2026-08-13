from datetime import datetime

import streamlit as st
from pypdf import PdfReader

import analyse
import pdf_verarbeitung
import retrieval
import speicher
from pdf_logik import (
    formatiere_quellenhinweis,
    frage_beantworten,
    relevanten_text_zusammenstellen,
    verwendete_quellen,
)


st.set_page_config(
    page_title="KI-PDF-Assistent",
    page_icon="📄",
    layout="wide",
)

st.markdown(
    """
    <style>
    .block-container {
        max-width: 900px;
        padding-top: 2.5rem;
        padding-bottom: 3rem;
    }
    [data-testid="stChatMessage"] {
        border-radius: 14px;
    }
    [data-testid="stSidebar"] .block-container {
        padding-top: 2rem;
    }
    h1, h2, h3 {
        letter-spacing: -0.01em;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


speicher.datenbank_initialisieren()


if "aktueller_chat_id" not in st.session_state:
    vorhandene_chats = speicher.chat_liste()
    st.session_state.aktueller_chat_id = (
        vorhandene_chats[0]["id"] if vorhandene_chats else speicher.chat_erstellen()
    )


with st.sidebar:
    st.markdown("## 📄 KI-PDF-Assistent")

    bereich = st.radio(
        "Bereich",
        ["💬 Chat", "🔍 Analyse & Vergleich"],
        key="aktiver_bereich",
        label_visibility="collapsed",
    )

    st.divider()

    if bereich == "💬 Chat":
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
    )

    for datei in hochgeladene_dateien or []:
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

    st.divider()
    st.markdown("#### 📚 Dokumentenbibliothek")

    dokumente = speicher.dokumente_laden()

    # Im Chat-Bereich bestimmt der aktuelle Chat, welche Checkboxen als
    # ausgewählt gelten. Die Checkbox-Keys sind bewusst pro Chat UND
    # Dokument vergeben (statt nur pro Dokument), damit Streamlits
    # eigener Widget-State beim Chatwechsel nicht die Auswahl eines
    # anderen Chats "durchschlagen" lässt. Im Analyse-Bereich ist die
    # Dokumentauswahl bewusst unabhängig davon (siehe Hauptbereich unten)
    # und wird hier nur informativ, ohne Checkboxen, aufgelistet.
    chat_bereich = bereich == "💬 Chat"

    if chat_bereich:
        aktueller_chat_id = st.session_state.aktueller_chat_id
        aktueller_chat = speicher.chat_laden(aktueller_chat_id)
        aktive_dokument_ids = set(aktueller_chat["dokument_ids"])

    if not dokumente:
        st.caption("Noch keine Dokumente in der Bibliothek.")
    else:
        if chat_bereich:
            st.caption(
                f"{len(aktive_dokument_ids)} von {len(dokumente)} Dokumenten "
                "für diesen Chat ausgewählt."
            )
        else:
            st.caption(
                f"{len(dokumente)} Dokument(e) in der Bibliothek. Die "
                "Auswahl für die Analyse erfolgt oben im Hauptbereich."
            )

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


if bereich == "💬 Chat":
    aktueller_chat = speicher.chat_laden(st.session_state.aktueller_chat_id)
    alle_dokumente = {dokument["id"]: dokument for dokument in speicher.dokumente_laden()}
    aktive_dokumente = [
        alle_dokumente[i] for i in aktueller_chat["dokument_ids"] if i in alle_dokumente
    ]

    st.title(aktueller_chat["titel"])

    if not alle_dokumente:
        st.info("Lade links mindestens ein PDF hoch, um Fragen stellen zu können.")
    elif not aktive_dokumente:
        st.info(
            "Wähle links in der Dokumentbibliothek mindestens ein Dokument für "
            "diesen Chat aus."
        )
    else:
        aktive_namen = ", ".join(dokument["dateiname"] for dokument in aktive_dokumente)
        st.caption(f"Aktive Dokumente: {aktive_namen}")

        for nachricht in aktueller_chat["nachrichten"]:
            with st.chat_message("user"):
                st.write(nachricht["frage"])

            with st.chat_message("assistant"):
                st.write(nachricht["antwort"])

                quellenhinweis = formatiere_quellenhinweis(nachricht["quellen"])

                if quellenhinweis:
                    st.caption(quellenhinweis)

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

                    quellenhinweis = formatiere_quellenhinweis(ausgewaehlte_quellen)

                    if quellenhinweis:
                        st.caption(quellenhinweis)

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

else:
    st.title("🔍 Analyse & Vergleich")
    st.caption(
        "Wähle Dokumente aus deiner Bibliothek aus und lasse sie strukturiert "
        "zusammenfassen, vergleichen oder auf Fristen und Risiken untersuchen."
    )

    alle_dokumente_liste = speicher.dokumente_laden()

    if not alle_dokumente_liste:
        st.info("Lade links mindestens ein PDF hoch, um eine Analyse zu starten.")
    else:
        namen_je_id = {d["id"]: d["dateiname"] for d in alle_dokumente_liste}
        verfuegbare_ids = list(namen_je_id.keys())

        # Die Analyse-Auswahl wird bewusst in einer eigenen, manuell
        # gepflegten Session-State-Variable gehalten (statt sich allein
        # auf den Widget-Key zu verlassen): Da das Multiselect nur im
        # Analyse-Bereich gerendert wird, würde Streamlit den
        # Widget-State sonst löschen, sobald zwischenzeitlich der
        # Chat-Bereich angezeigt wird (Widgets, die in einem Run nicht
        # gezeichnet werden, verlieren ihren Zustand). Die separate
        # Variable übersteht das und wird beim erneuten Rendern über
        # `default=` wieder eingesetzt.
        if "analyse_dokument_ids" not in st.session_state:
            st.session_state.analyse_dokument_ids = []

        # Gespeicherte Auswahl auf noch existierende Dokumente begrenzen,
        # bevor das Widget gerendert wird - sonst wirft st.multiselect
        # einen Fehler, falls zwischenzeitlich ein ausgewähltes Dokument
        # gelöscht wurde.
        st.session_state.analyse_dokument_ids = [
            i for i in st.session_state.analyse_dokument_ids if i in verfuegbare_ids
        ]

        ausgewaehlte_ids = st.multiselect(
            "Dokumente für die Analyse auswählen",
            options=verfuegbare_ids,
            default=st.session_state.analyse_dokument_ids,
            format_func=lambda i: namen_je_id.get(i, str(i)),
            key="analyse_dokument_ids_widget",
            help=(
                "Diese Auswahl ist unabhängig von der Dokumentauswahl "
                "einzelner Chats."
            ),
        )

        st.session_state.analyse_dokument_ids = ausgewaehlte_ids

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
                with st.container(border=True):
                    st.markdown(f"**{aktion['icon']} {aktion['titel']}**")
                    st.caption(aktion["beschreibung"])

                    zu_wenige = len(ausgewaehlte_ids) < aktion["mindestens"]

                    if st.button(
                        "Ausführen",
                        key=f"analyse_start_{aktion['modus']}",
                        use_container_width=True,
                        disabled=zu_wenige,
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

                    if zu_wenige:
                        st.caption(
                            f"Benötigt mindestens {aktion['mindestens']} "
                            f"Dokument{'e' if aktion['mindestens'] > 1 else ''}."
                        )

        st.divider()

        ergebnis = st.session_state.analyse_ergebnis

        if not ergebnis:
            st.info("Wähle oben Dokumente aus und starte eine Analyse.")
        else:
            kopf_spalte, reset_spalte = st.columns([5, 2])
            kopf_spalte.markdown(f"### {ergebnis['icon']} {ergebnis['titel']}")

            if reset_spalte.button(
                "🗑️ Ergebnis leeren",
                key="analyse_ergebnis_leeren",
                use_container_width=True,
            ):
                st.session_state.analyse_ergebnis = None
                st.rerun()

            ergebnis_namen = ", ".join(
                namen_je_id.get(i, "(gelöscht)") for i in ergebnis["dokument_ids"]
            )
            st.caption(f"Dokumente: {ergebnis_namen}")

            if ergebnis["modus"] == "risiken":
                st.warning(analyse.RISIKEN_HINWEIS)

            with st.container(border=True):
                st.markdown(ergebnis["daten"]["text"])

            if ergebnis["daten"]["quellenhinweis"]:
                st.caption(ergebnis["daten"]["quellenhinweis"])

            st.divider()
            st.markdown("#### 💬 Rückfragen zur Analyse")
            st.caption(
                "Stelle Rückfragen zu diesem Ergebnis - unabhängig von "
                "deinen normalen Chats."
            )

            for eintrag in ergebnis["rueckfragen"]:
                with st.chat_message("user"):
                    st.write(eintrag["frage"])

                with st.chat_message("assistant"):
                    st.write(eintrag["antwort"])

                    if eintrag["quellenhinweis"]:
                        st.caption(eintrag["quellenhinweis"])

            rueckfrage = st.chat_input("Frage zur Analyse stellen...")

            if rueckfrage:
                with st.chat_message("user"):
                    st.write(rueckfrage)

                try:
                    with st.chat_message("assistant"):
                        with st.spinner("Antwort wird erstellt..."):
                            rueckfrage_ergebnis = analyse.rueckfrage_beantworten(
                                ergebnis["daten"]["text"],
                                ergebnis["dokument_ids"],
                                rueckfrage,
                                verlauf=ergebnis["rueckfragen"],
                            )

                        st.write(rueckfrage_ergebnis["text"])

                        if rueckfrage_ergebnis["quellenhinweis"]:
                            st.caption(rueckfrage_ergebnis["quellenhinweis"])

                    ergebnis["rueckfragen"].append(
                        {
                            "frage": rueckfrage,
                            "antwort": rueckfrage_ergebnis["text"],
                            "quellenhinweis": rueckfrage_ergebnis["quellenhinweis"],
                        }
                    )
                    st.session_state.analyse_ergebnis = ergebnis

                    st.rerun()

                except Exception as fehler:
                    st.error("Die Rückfrage ist fehlgeschlagen.")
                    st.caption(f"Technische Details: {fehler}")
