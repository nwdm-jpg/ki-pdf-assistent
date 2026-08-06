import streamlit as st
from pypdf import PdfReader
from openai import OpenAI


st.title("KI-PDF-Assistent")

hochgeladene_datei = st.file_uploader(
    "PDF-Datei auswählen",
    type=["pdf"],
)

if hochgeladene_datei is not None:
    reader = PdfReader(hochgeladene_datei)

    st.success(f"Datei geladen: {hochgeladene_datei.name}")
    st.write(f"Anzahl Seiten: {len(reader.pages)}")

    gesamter_text = ""

    for nummer, seite in enumerate(reader.pages, start=1):
        text = seite.extract_text()

        if text:
            gesamter_text += f"\n--- Seite {nummer} ---\n{text}"

    st.write(f"Anzahl Zeichen: {len(gesamter_text)}")

    with st.expander("Extrahierten Text anzeigen"):
        st.text(gesamter_text)

    
    client = OpenAI()

    frage = st.text_input("Welche Frage möchtest du zur PDF stellen?")

    if frage:
        antwort = client.responses.create(
            model="gpt-5-mini",
            input=[
                {
                    "role": "system",
                    "content": (
                        "Beantworte die Frage ausschließlich anhand des "
                        "bereitgestellten PDF-Textes. Wenn die Antwort nicht "
                        "im Text steht, sage das klar."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"PDF-Text:\n{gesamter_text}\n\n"
                        f"Frage: {frage}"
                    ),
                },
            ],
        )

        st.subheader("Antwort")
        st.write(antwort.output_text)