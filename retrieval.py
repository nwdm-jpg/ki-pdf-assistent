"""Semantische Suche über Dokument-Chunks mittels OpenAI-Embeddings.

Ergänzt (statt ersetzt) die einfache Stichwortsuche aus pdf_logik.py:
web_app.py nutzt dieses Modul für die dokumentübergreifende Chunk-Suche,
app.py (CLI) bleibt bei der einfachen Seiten-Stichwortsuche.

Nutzt den in pdf_logik.py bereits konfigurierten OpenAI-Client, damit es
nur eine Stelle mit Client-/API-Key-Handling gibt.
"""

import re

import numpy as np

from pdf_logik import STOPPWOERTER, client


EMBEDDING_MODELL = "text-embedding-3-small"

# Kleines Gewicht für den Stichwort-Bonus: Embeddings sind das
# Hauptkriterium, exakte Begriffstreffer (Paragrafen, Produktnamen, ...)
# sollen aber nicht allein durch Embedding-Ähnlichkeit untergehen.
KEYWORD_GEWICHT = 0.15


def embedding_erstellen(text):
    """Erstellt das Embedding für einen einzelnen Text (z. B. eine Frage)."""
    antwort = client.embeddings.create(model=EMBEDDING_MODELL, input=text)
    return antwort.data[0].embedding


def embeddings_batch_erstellen(texte):
    """Erstellt Embeddings für mehrere Texte in einem einzigen API-Aufruf.

    Wird beim Verarbeiten eines neu hochgeladenen Dokuments verwendet, um
    nicht pro Chunk einen eigenen API-Aufruf zu machen.
    """
    if not texte:
        return []

    antwort = client.embeddings.create(model=EMBEDDING_MODELL, input=texte)
    return [eintrag.embedding for eintrag in antwort.data]


def _kosinus_aehnlichkeiten(frage_vektor, chunk_matrix):
    """Vektorisierte Kosinus-Ähnlichkeit zwischen einer Frage und vielen Chunks."""
    frage_norm = frage_vektor / (np.linalg.norm(frage_vektor) + 1e-10)
    chunk_normen = np.linalg.norm(chunk_matrix, axis=1) + 1e-10
    chunk_normiert = chunk_matrix / chunk_normen[:, None]

    return chunk_normiert @ frage_norm


def relevante_chunks_ermitteln(frage, chunks, anzahl_pro_dokument=3, zusatzkontext=""):
    """Findet die relevantesten Chunks je Dokument per Embedding-Ähnlichkeit.

    Kombiniert die Kosinus-Ähnlichkeit zwischen Frage- und Chunk-Embedding
    (Hauptkriterium) mit einem kleinen Bonus für Stichwortüberschneidung.
    Wie bei der ursprünglichen Stichwortsuche (siehe
    `pdf_logik.relevante_seiten_ermitteln`) erfolgt die Auswahl bewusst
    pro Dokument, damit bei Rückfragen kein Dokument durch
    Kontext-Überschneidung komplett verdrängt wird.

    `zusatzkontext` (z. B. der letzte Chatverlauf) fließt in Embedding
    und Stichwortabgleich mit ein, damit auch vokabelarme Rückfragen noch
    die passenden Chunks finden.

    Erwartet für jeden Eintrag in `chunks` mindestens die Felder
    "dateiname", "seitennummer", "text" und "embedding" (z. B. wie von
    `speicher.chunks_laden` geliefert). Gibt eine flache Liste von
    Chunk-Einträgen zurück (ohne das "embedding"-Feld zu entfernen).
    """
    if not chunks:
        return []

    suchtext = f"{zusatzkontext}\n{frage}" if zusatzkontext else frage
    frage_vektor = np.asarray(embedding_erstellen(suchtext), dtype=np.float32)

    frage_woerter = {
        wort
        for wort in re.findall(r"\w+", suchtext.lower())
        if wort not in STOPPWOERTER
    }

    chunks_je_dokument = {}

    for eintrag in chunks:
        chunks_je_dokument.setdefault(eintrag["dateiname"], []).append(eintrag)

    ergebnis = []

    for dokument_chunks in chunks_je_dokument.values():
        chunk_matrix = np.array(
            [eintrag["embedding"] for eintrag in dokument_chunks], dtype=np.float32
        )
        aehnlichkeiten = _kosinus_aehnlichkeiten(frage_vektor, chunk_matrix)

        bewertete_chunks = []

        for eintrag, aehnlichkeit in zip(dokument_chunks, aehnlichkeiten):
            chunk_woerter = set(re.findall(r"\w+", eintrag["text"].lower()))
            ueberschneidung = len(frage_woerter & chunk_woerter) / max(
                1, len(frage_woerter)
            )
            score = float(aehnlichkeit) + KEYWORD_GEWICHT * ueberschneidung

            bewertete_chunks.append((score, eintrag))

        bewertete_chunks.sort(key=lambda paar: paar[0], reverse=True)

        ergebnis.extend(eintrag for _, eintrag in bewertete_chunks[:anzahl_pro_dokument])

    return ergebnis
