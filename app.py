from pathlib import Path

from pypdf import PdfReader

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
        suchbegriff = input(
            "\nWonach möchtest du suchen? "
            "(Zum Beenden: ende) "
        )

        if suchbegriff.lower() == "ende":
            print("Programm beendet.")
            break

        text_klein = gesamter_text.lower()
        suchbegriff_klein = suchbegriff.lower()

        position = text_klein.find(suchbegriff_klein)

        if position != -1:
            start = max(0, position - 120)
            ende = min(
                len(gesamter_text),
                position + len(suchbegriff) + 120,
            )

            textstelle = gesamter_text[start:ende]

            print(f'\nDer Begriff "{suchbegriff}" wurde gefunden.')
            print("\nPassende Textstelle:")
            print(textstelle)
        else:
            print(f'Der Begriff "{suchbegriff}" wurde nicht gefunden.')