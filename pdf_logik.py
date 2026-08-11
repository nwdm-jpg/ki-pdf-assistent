"""Gemeinsame Logik für PDF-Verarbeitung, Seiten-Retrieval und KI-Anfragen.

Wird sowohl von app.py (CLI) als auch von web_app.py (Streamlit) verwendet.
"""

import re

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

MODELL = "gpt-5-mini"

# Der OPENAI_API_KEY wird von OpenAI() automatisch aus der Umgebung gelesen.
client = OpenAI()


def pdf_seiten_extrahieren(reader):
    """Extrahiert den Text aller Seiten eines PdfReader.

    Gibt (gesamter_text, seiten_texte) zurück, wobei seiten_texte eine
    Liste von (seitennummer, seitentext) für Seiten mit extrahierbarem
    Text ist.
    """
    gesamter_text = ""
    seiten_texte = []

    for nummer, seite in enumerate(reader.pages, start=1):
        text = seite.extract_text()

        if text:
            gesamter_text += f"\n--- Seite {nummer} ---\n{text}"
            seiten_texte.append((nummer, text))

    return gesamter_text, seiten_texte


def relevante_seiten_ermitteln(frage, seiten_texte, anzahl=3):
    """Bewertet Seiten anhand der Wortüberschneidung mit der Frage.

    Gibt die `anzahl` besten Seiten als Liste von
    (treffer, seitennummer, seitentext) zurück, absteigend sortiert.
    """
    frage_woerter = {
        wort
        for wort in re.findall(r"\w+", frage.lower())
        if wort not in STOPPWOERTER
    }

    bewertete_seiten = []

    for seitennummer, seitentext in seiten_texte:
        seiten_woerter = set(re.findall(r"\w+", seitentext.lower()))

        treffer = len(frage_woerter & seiten_woerter)

        bewertete_seiten.append((treffer, seitennummer, seitentext))

    return sorted(bewertete_seiten, reverse=True)[:anzahl]


def relevanten_text_zusammenstellen(beste_seiten):
    """Baut aus den besten Seiten den Text für den KI-Prompt zusammen."""
    return "\n\n".join(
        f"--- Seite {seitennummer} ---\n{seitentext}"
        for _, seitennummer, seitentext in beste_seiten
    )


def verwendete_seitennummern(beste_seiten):
    """Extrahiert die Seitennummern aus den besten Seiten."""
    return [seitennummer for _, seitennummer, _ in beste_seiten]


def frage_beantworten(frage, relevanter_text):
    """Stellt die Frage zusammen mit dem relevanten PDF-Text an die KI."""
    antwort = client.responses.create(
        model=MODELL,
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

    return antwort.output_text
