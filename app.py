import re
from pathlib import Path

from openai import OpenAI
from pypdf import PdfReader


client = OpenAI()

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

dateiname = input("Wie heißt die PDF-Datei? ")
pdf_pfad = Path("pdfs") / dateiname

if not pdf_pfad.exists():
    print(f"Fehler: Die Datei wurde nicht gefunden: {pdf_pfad}")

else:
    reader = PdfReader(pdf_pfad)

    print("PDF erfolgreich geöffnet.")
    print(f"Anzahl Seiten: {len(reader.pages)}")

    gesamter_text = ""
    seiten_texte = []

    for nummer, seite in enumerate(reader.pages, start=1):
        text = seite.extract_text()

        if text:
            gesamter_text += f"\n--- Seite {nummer} ---\n{text}"
            seiten_texte.append((nummer, text))

    print("PDF wurde vollständig eingelesen.")
    print(f"Anzahl Zeichen: {len(gesamter_text)}")

    while True:
        frage = input(
            "\nWelche Frage möchtest du zur PDF stellen? "
            "(Zum Beenden: ende) "
        )

        if frage.lower() == "ende":
            print("Programm beendet.")
            break

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

        print("\nAntwort:")
        print(antwort.output_text)