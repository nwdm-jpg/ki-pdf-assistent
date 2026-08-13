import streamlit as st
from pypdf import PdfReader

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
    st.markdown("#### Dokumentbibliothek")
    st.caption("Hochgeladene PDFs bleiben dauerhaft gespeichert.")

    hochgeladene_dateien = st.file_uploader(
        "PDF-Dateien hinzufügen",
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

    dokumente = speicher.dokumente_laden()
    aktueller_chat = speicher.chat_laden(st.session_state.aktueller_chat_id)
    aktive_dokument_ids = set(aktueller_chat["dokument_ids"])

    if dokumente:
        neue_aktive_ids = set()

        for dokument in dokumente:
            spalte_check, spalte_info, spalte_loeschen = st.columns([1, 4, 1])

            ausgewaehlt = spalte_check.checkbox(
                "aktiv",
                value=dokument["id"] in aktive_dokument_ids,
                key=f"aktiv_{dokument['id']}",
                label_visibility="collapsed",
            )

            if ausgewaehlt:
                neue_aktive_ids.add(dokument["id"])

            seiten_wort = "Seite" if dokument["seitenzahl"] == 1 else "Seiten"
            spalte_info.markdown(
                f"**{dokument['dateiname']}**  \n"
                f"{dokument['seitenzahl']} {seiten_wort} · "
                f"{dokument['hochgeladen_am'][:10]}"
            )

            if spalte_loeschen.button("🗑", key=f"del_doc_{dokument['id']}"):
                speicher.dokument_loeschen(dokument["id"])
                st.rerun()

        if neue_aktive_ids != aktive_dokument_ids:
            speicher.chat_dokumente_setzen(
                st.session_state.aktueller_chat_id, sorted(neue_aktive_ids)
            )
            st.rerun()
    else:
        st.caption("Noch keine Dokumente in der Bibliothek.")


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
