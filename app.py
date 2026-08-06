from pathlib import Path

from pypdf import PdfReader

from openai import OpenAI

client = OpenAI()

dateiname = input("Wie heißt die PDF-Datei? ")
pdf_pfad = Path("pdfs") / dateiname

if not pdf_pfad.exists():
    print(f"Fehler: Die Datei wurde nicht gefunden: {pdf_pfad}")
else:
    reader = PdfReader(pdf_pfad)

    print("PDF erfolgreich geöffnet.")
    print(f"Anzahl Seiten: {len(reader.pages)}")

    gesamter_text = ""

    for nummer, seite in enumerate(reader.pages, start=1):
        text = seite.extract_text()

        if text:
            gesamter_text += f"\n--- Seite {nummer} ---\n{text}"

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

        print("\nAntwort:")
        print(antwort.output_text)