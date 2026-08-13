"""PDF-Textextraktion und Aufteilung in Chunks für die semantische Suche.

Ergänzt die seitenweise Extraktion aus pdf_logik.py um eine Aufteilung
jeder Seite in kleinere, überlappende Text-Chunks, damit die
Embedding-Suche gezielter auf einzelne Absätze statt ganze Seiten
zugreifen kann.
"""

import re


CHUNK_GROESSE = 1000
CHUNK_UEBERLAPPUNG = 150


def text_in_chunks_aufteilen(text, chunk_groesse=CHUNK_GROESSE, ueberlappung=CHUNK_UEBERLAPPUNG):
    """Teilt einen Text in überlappende Chunks, bevorzugt an Satzgrenzen.

    Kurze Texte (kürzer als `chunk_groesse`) werden unverändert als
    einzelner Chunk zurückgegeben.
    """
    text = text.strip()

    if not text:
        return []

    if len(text) <= chunk_groesse:
        return [text]

    saetze = re.split(r"(?<=[.!?])\s+", text)

    chunks = []
    aktueller_chunk = ""

    for satz in saetze:
        if aktueller_chunk and len(aktueller_chunk) + len(satz) + 1 > chunk_groesse:
            chunks.append(aktueller_chunk.strip())
            ueberlapp_text = aktueller_chunk[-ueberlappung:]
            aktueller_chunk = f"{ueberlapp_text} {satz}".strip()
        else:
            aktueller_chunk = f"{aktueller_chunk} {satz}".strip()

    if aktueller_chunk.strip():
        chunks.append(aktueller_chunk.strip())

    return chunks


def dokument_chunks_erstellen(reader, dateiname):
    """Extrahiert den Text aller Seiten eines PdfReader und teilt ihn in Chunks.

    Gibt eine Liste von Dicts {"dateiname", "seitennummer", "text"} zurück,
    wobei jeder Eintrag einem Text-Chunk (nicht einer ganzen Seite)
    entspricht. Seiten ohne extrahierbaren Text werden übersprungen.
    """
    chunks = []

    for seitennummer, seite in enumerate(reader.pages, start=1):
        text = seite.extract_text()

        if not text:
            continue

        for chunk_text in text_in_chunks_aufteilen(text):
            chunks.append(
                {
                    "dateiname": dateiname,
                    "seitennummer": seitennummer,
                    "text": chunk_text,
                }
            )

    return chunks
