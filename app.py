from pathlib import Path

from pypdf import PdfReader

from pdf_logik import (
    frage_beantworten,
    pdf_seiten_extrahieren,
    relevante_seiten_ermitteln,
    relevanten_text_zusammenstellen,
    verwendete_quellen,
)


dateiname = input("Wie heißt die PDF-Datei? ")
pdf_pfad = Path("pdfs") / dateiname

if not pdf_pfad.exists():
    print(f"Fehler: Die Datei wurde nicht gefunden: {pdf_pfad}")

else:
    reader = PdfReader(pdf_pfad)

    print("PDF erfolgreich geöffnet.")
    print(f"Anzahl Seiten: {len(reader.pages)}")

    seiten = pdf_seiten_extrahieren(reader, dateiname)
    anzahl_zeichen = sum(len(eintrag["text"]) for eintrag in seiten)

    print("PDF wurde vollständig eingelesen.")
    print(f"Anzahl Zeichen: {anzahl_zeichen}")

    while True:
        frage = input(
            "\nWelche Frage möchtest du zur PDF stellen? "
            "(Zum Beenden: ende) "
        )

        if frage.lower() == "ende":
            print("Programm beendet.")
            break

        beste_seiten = relevante_seiten_ermitteln(frage, seiten)
        relevanter_text = relevanten_text_zusammenstellen(beste_seiten)
        ausgewaehlte_seiten = [
            seitennummer
            for _, seitennummer in verwendete_quellen(beste_seiten)
        ]

        print(
            "Verwendete Seiten:",
            ", ".join(map(str, ausgewaehlte_seiten)),
        )

        antwort_text = frage_beantworten(frage, relevanter_text)

        print("\nAntwort:")
        print(antwort_text)
