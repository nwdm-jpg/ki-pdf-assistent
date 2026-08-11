import streamlit as st
from pypdf import PdfReader

from pdf_logik import (
    frage_beantworten,
    pdf_seiten_extrahieren,
    relevante_seiten_ermitteln,
    relevanten_text_zusammenstellen,
    verwendete_seitennummern,
)


if "chat_verlauf" not in st.session_state:
    st.session_state.chat_verlauf = []

st.title("KI-PDF-Assistent")

hochgeladene_datei = st.file_uploader(
    "PDF-Datei hinzufügen",
    type=["pdf"],
)

if hochgeladene_datei is not None:
    try:
        reader = PdfReader(hochgeladene_datei)
    except Exception as fehler:
        st.error("Die PDF konnte nicht gelesen werden.")
        st.caption(f"Technische Details: {fehler}")
        st.stop()

    st.success(f"Datei geladen: {hochgeladene_datei.name}")
    st.write(f"Anzahl Seiten: {len(reader.pages)}")

    gesamter_text, seiten_texte = pdf_seiten_extrahieren(reader)

    st.write(f"Anzahl Zeichen: {len(gesamter_text)}")

    with st.expander("Extrahierten Text anzeigen"):
        st.text(gesamter_text)

    st.divider()

    if st.button("🗑️ Chat leeren"):
        st.session_state.chat_verlauf = []
        st.rerun()

    for eintrag in st.session_state.chat_verlauf:
        with st.chat_message("user"):
            st.write(eintrag["frage"])

        with st.chat_message("assistant"):
            st.write(eintrag["antwort"])

            if "seiten" in eintrag:
                st.caption(
                    "Verwendete Seiten: "
                    + ", ".join(map(str, eintrag["seiten"]))
                )

    frage = st.chat_input("Stelle eine Frage zur PDF...")

    if frage:
        with st.chat_message("user"):
            st.write(frage)

        beste_seiten = relevante_seiten_ermitteln(frage, seiten_texte)
        relevanter_text = relevanten_text_zusammenstellen(beste_seiten)
        ausgewaehlte_seiten = verwendete_seitennummern(beste_seiten)

        try:
            with st.chat_message("assistant"):
                with st.spinner("Die KI analysiert die PDF..."):
                    antwort_text = frage_beantworten(frage, relevanter_text)

            st.session_state.chat_verlauf.append(
                {
                    "frage": frage,
                    "antwort": antwort_text,
                    "seiten": ausgewaehlte_seiten,
                }
            )

            st.rerun()

        except Exception as fehler:
            st.error("Die KI-Anfrage ist fehlgeschlagen.")
            st.caption(f"Technische Details: {fehler}")
