import re
import streamlit as st
from pypdf import PdfReader
from openai import OpenAI

STOPPWOERTER = {
    "der",
    "die",
    "das",
    "ein",
    "eine",
    "und",
    "oder",
    "ist",
    "sind",
    "im",
    "in",
    "am",
    "an",
    "auf",
    "zu",
    "zur",
    "zum",
    "mit",
    "von",
    "für",
    "welche",
    "was",
    "wie",
}


st.title("KI-PDF-Assistent")

hochgeladene_datei = st.file_uploader(
    "PDF-Datei auswählen",
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

    gesamter_text = ""

    seiten_texte =[]

    for nummer, seite in enumerate(reader.pages, start=1):
        text = seite.extract_text()

        if text:
            gesamter_text += f"\n--- Seite {nummer} ---\n{text}"
            seiten_texte.append((nummer, text))

    st.write(f"Anzahl Zeichen: {len(gesamter_text)}")

    with st.expander("Extrahierten Text anzeigen"):
        st.text(gesamter_text)

    
    client = OpenAI()

    frage = st.text_input("Welche Frage möchtest du zur PDF stellen?")

    if frage:
        frage_woerter = {
            wort
            for wort in re.findall(r"\w+", frage.lower())
            if wort not in STOPPWOERTER
        }

        bewertete_seiten = []

        for seitennummer, seitentext in seiten_texte:
            seiten_woerter = set(
                re.findall(r"\w+", seitentext.lower())
            )

            treffer = len(frage_woerter & seiten_woerter)

            bewertete_seiten.append(
                (treffer, seitennummer, seitentext)
            )

        beste_seiten = sorted(
            bewertete_seiten,
            reverse=True,
        )[:3]

        relevanter_text = "\n\n".join(
            f"--- Seite {seitennummer} ---\n{seitentext}"
            for _, seitennummer, seitentext in beste_seiten
        )

        ausgewaehlte_seiten = [
            seitennummer
            for _, seitennummer, _ in beste_seiten
        ]

        st.write(
            "Verwendete Seiten:",
            ", ".join(map(str, ausgewaehlte_seiten)),
        )

        try:
            with st.spinner("Die KI analysiert die PDF..."):
                antwort = client.responses.create(
                    model="gpt-5-mini",
                    input=[
                        {
                            "role": "system",
                            "content": (
                                "Beantworte die Frage ausschließlich anhand der "
                                "bereitgestellten PDF-Seiten. Wenn die Antwort nicht "
                                "im Text steht, sage das klar."
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                f"Relevante PDF-Seiten:\n{relevanter_text}\n\n"
                                f"Frage: {frage}"
                            ),
                        },
                    ],
                )

            st.subheader("Antwort")
            st.write(antwort.output_text)

        except Exception as fehler:
            st.error("Die KI-Anfrage ist fehlgeschlagen.")
            st.caption(f"Technische Details: {fehler}")