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