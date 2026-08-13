"""Analyse- und Vergleichsfunktionen für die persistente Dokumentbibliothek.

Eigenständiges Modul für den "Analyse & Vergleich"-Bereich von
web_app.py: Zusammenfassen, Vergleichen, Fristen-Extraktion und
Risikoanalyse über beliebig viele Dokumente aus der Bibliothek.

Nutzt bewusst dieselbe Infrastruktur wie der Chat, statt eigene Logik
aufzubauen:
- `speicher.chunks_laden` für bereits gespeicherte Chunks + Embeddings
  (keine erneute Embedding-Erzeugung).
- `retrieval.relevante_chunks_ermitteln` für die semantische Auswahl der
  relevantesten Ausschnitte je Dokument (begrenzt Kontextgröße/Kosten,
  statt ganze PDFs an das Modell zu senden).
- `pdf_logik.relevanten_text_zusammenstellen` / `verwendete_quellen` /
  `formatiere_quellenhinweis` für Prompt-Aufbau und Quellenangaben,
  sowie den gemeinsamen OpenAI-Client (`pdf_logik.client`).

Da eine Analyse (anders als eine Chat-Frage) keine konkrete Nutzerfrage
hat, dient je Analyseart eine feste, themenbezogene "Suchanfrage" als
Grundlage für die Embedding-Suche.
"""

import speicher
from pdf_logik import (
    MODELL,
    client,
    formatiere_quellenhinweis,
    relevanten_text_zusammenstellen,
    verwendete_quellen,
)
from retrieval import relevante_chunks_ermitteln


# Wie viele Chunks je Dokument maximal in eine Analyse einfließen.
# Höher als beim Chat (der auf eine konkrete Frage antwortet), damit
# Zusammenfassung/Vergleich/Fristen/Risiken eine breitere Abdeckung des
# Dokuments erhalten - aber weiterhin begrenzt, um Kontextgröße und
# Kosten in einem sinnvollen Rahmen zu halten.
CHUNKS_PRO_DOKUMENT = 8

RISIKEN_HINWEIS = (
    "⚠️ Diese Analyse hebt potenziell relevante Textstellen hervor und "
    "ersetzt keine rechtliche, finanzielle oder professionelle Beratung."
)

_QUELLENFORMAT_HINWEIS = (
    'Belege wichtige Aussagen direkt im Text mit der Quelle im Format '
    '"(Quelle: Dateiname, Seite X)", basierend auf den Kennzeichnungen '
    "der Dokumentausschnitte. Nutze ausschließlich die bereitgestellten "
    "Ausschnitte als Informationsquelle und erfinde keine Informationen, "
    "Daten oder Quellenangaben."
)

ZUSAMMENFASSEN_SUCHANFRAGE = (
    "Hauptthema, Zusammenfassung, wichtige Punkte, Bedingungen, Beträge, "
    "Zahlen, Pflichten, Rechte, wichtige Klauseln"
)
ZUSAMMENFASSEN_SYSTEM = (
    "Du fasst Dokumente auf Basis der bereitgestellten Ausschnitte "
    "strukturiert zusammen. Wenn Ausschnitte aus mehreren Dokumenten "
    "vorliegen, erstelle für jedes Dokument einen eigenen Abschnitt mit "
    "dem Dateinamen als Überschrift.\n\n"
    "Gliedere die Zusammenfassung je Dokument, soweit im Text vorhanden, "
    "in: Hauptthema, Wichtige Punkte, Wichtige Bedingungen, Wichtige "
    "Zahlen/Beträge, Pflichten, sowie auffällige Klauseln oder Hinweise. "
    "Lasse Abschnitte weg, für die die Ausschnitte keine Information "
    "enthalten.\n\n"
    f"{_QUELLENFORMAT_HINWEIS} Antworte auf Deutsch in Markdown mit "
    "Überschriften."
)

VERGLEICHEN_SUCHANFRAGE = (
    "Laufzeit, Kündigungsfrist, Kosten, Preise, Leistungen, Pflichten, "
    "Haftung, Datenschutz, Support, Fristen, Verfügbarkeit, Bedingungen"
)
VERGLEICHEN_SYSTEM = (
    "Du vergleichst mehrere Dokumente strukturiert anhand der "
    "bereitgestellten Ausschnitte.\n\n"
    "Identifiziere eigenständig sinnvolle Vergleichskategorien anhand des "
    "tatsächlichen Inhalts der Dokumente (z. B. Laufzeit, "
    "Kündigungsfrist, Kosten/Preise, Leistungen, Pflichten, Haftung, "
    "Datenschutz, Support, Fristen, Verfügbarkeit o. ä.). Erzwinge keine "
    "Kategorie, zu der die Ausschnitte nichts hergeben, und ergänze bei "
    "Bedarf andere relevante Kategorien.\n\n"
    "Stelle den Vergleich als Markdown-Tabelle dar (eine Zeile je "
    "Kategorie, eine Spalte je Dokument). Ergänze danach einen Abschnitt "
    '"## Wichtigste Unterschiede" mit den bedeutendsten Unterschieden '
    "zwischen den Dokumenten als Stichpunkte.\n\n"
    f"{_QUELLENFORMAT_HINWEIS} Antworte auf Deutsch."
)

FRISTEN_SUCHANFRAGE = (
    "Datum, Frist, Kündigungsfrist, Vertragslaufzeit, Verlängerung, "
    "Zahlungsfrist, Termin, Stichtag, Gültigkeitsdauer"
)
FRISTEN_SYSTEM = (
    "Du extrahierst terminliche und fristbezogene Informationen aus den "
    "bereitgestellten Dokumentausschnitten: Daten, Fristen, "
    "Kündigungsfristen, Vertragslaufzeiten, Verlängerungsfristen, "
    "Zahlungsfristen und andere zeitkritische Verpflichtungen.\n\n"
    "Erfinde keine Daten oder Fristen, die nicht im Text belegt sind. "
    "Wenn ein Dokument keine erkennbaren Fristen enthält, erwähne das "
    "kurz statt etwas zu erfinden. Liste die gefundenen Punkte klar "
    "strukturiert und, wo möglich, chronologisch geordnet (frühester "
    "Termin zuerst) auf, gruppiert nach Dokument.\n\n"
    f"{_QUELLENFORMAT_HINWEIS} Antworte auf Deutsch in Markdown."
)

RISIKEN_SUCHANFRAGE = (
    "Haftung, Ausschluss, Einschränkung, Pflicht, Vertragsstrafe, "
    "Kündigung, Sonderregelung, ungewöhnliche Bedingung, Risiko, "
    "Gewährleistung"
)
RISIKEN_SYSTEM = (
    "Du analysierst die bereitgestellten Dokumentausschnitte auf "
    "potenziell wichtige, ungewöhnliche oder nachteilige Klauseln, "
    "Bedingungen, Pflichten, Haftungsregelungen, Einschränkungen oder "
    "Ausschlüsse, die besondere Aufmerksamkeit verdienen.\n\n"
    "Gruppiere die Punkte nach Dokument. Diese Analyse ist keine "
    "rechtliche, finanzielle oder professionelle Beratung, sondern "
    "hebt lediglich potenziell relevante Textstellen hervor - formuliere "
    "entsprechend vorsichtig (z. B. \"könnte relevant sein\", \"sollte "
    "geprüft werden\") statt abschließende Bewertungen abzugeben.\n\n"
    f"{_QUELLENFORMAT_HINWEIS} Antworte auf Deutsch in Markdown."
)


def _relevante_ausschnitte(dokument_ids, suchanfrage):
    chunks = speicher.chunks_laden(dokument_ids)
    return relevante_chunks_ermitteln(
        suchanfrage, chunks, anzahl_pro_dokument=CHUNKS_PRO_DOKUMENT
    )


def _analyse_durchfuehren(system_text, suchanfrage, dokument_ids):
    if not dokument_ids:
        raise ValueError("Es wurden keine Dokumente für die Analyse ausgewählt.")

    ausschnitte = _relevante_ausschnitte(dokument_ids, suchanfrage)

    if not ausschnitte:
        raise ValueError(
            "Für die ausgewählten Dokumente konnten keine Textausschnitte "
            "gefunden werden."
        )

    relevanter_text = relevanten_text_zusammenstellen(ausschnitte)

    nachrichten = [
        {"role": "system", "content": system_text},
        {
            "role": "user",
            "content": f"Dokumentausschnitte:\n{relevanter_text}",
        },
    ]

    antwort = client.responses.create(model=MODELL, input=nachrichten)

    quellen = verwendete_quellen(ausschnitte)

    return {
        "text": antwort.output_text,
        "quellen": quellen,
        "quellenhinweis": formatiere_quellenhinweis(quellen),
    }


def zusammenfassen(dokument_ids):
    """Erstellt eine strukturierte Zusammenfassung eines oder mehrerer Dokumente."""
    return _analyse_durchfuehren(
        ZUSAMMENFASSEN_SYSTEM, ZUSAMMENFASSEN_SUCHANFRAGE, dokument_ids
    )


def vergleichen(dokument_ids):
    """Vergleicht mindestens zwei Dokumente strukturiert (Tabelle + Unterschiede)."""
    dokument_ids = list(dict.fromkeys(dokument_ids))

    if len(dokument_ids) < 2:
        raise ValueError("Für einen Vergleich werden mindestens zwei Dokumente benötigt.")

    return _analyse_durchfuehren(
        VERGLEICHEN_SYSTEM, VERGLEICHEN_SUCHANFRAGE, dokument_ids
    )


def fristen_ermitteln(dokument_ids):
    """Extrahiert Fristen, Termine und zeitkritische Verpflichtungen."""
    return _analyse_durchfuehren(FRISTEN_SYSTEM, FRISTEN_SUCHANFRAGE, dokument_ids)


def risiken_ermitteln(dokument_ids):
    """Hebt potenziell wichtige/ungewöhnliche Klauseln und Bedingungen hervor."""
    return _analyse_durchfuehren(RISIKEN_SYSTEM, RISIKEN_SUCHANFRAGE, dokument_ids)
