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

# Gemeinsame Kürze-/Struktur-Vorgabe für alle vier Analysearten, damit
# die Ergebnisse sich einheitlich lesen (gleiche Überschriften, kompakt
# statt ausschweifend) und auf einen Blick erfassbar bleiben. Wer mehr
# Detail möchte, kann in der Rückfragen-Chat nachfragen.
_KUERZE_HINWEIS = (
    "Antworte sehr knapp: keine lange Einleitung, keine Wiederholungen, "
    "keine ausschweifende oder juristisch anmutende Sprache. Nutze kurze "
    "Stichpunkte und, wo sinnvoll, kompakte Tabellen statt Fließtext. Das "
    "Ergebnis soll ohne langes Scrollen erfassbar sein - Details kann der "
    "Nutzer in einer Rückfrage erfragen."
)
_STRUKTUR_HINWEIS = (
    "Gliedere die Antwort so:\n"
    "## Kernergebnis\n"
    "1-2 Sätze mit dem wichtigsten Ergebnis.\n\n"
    "## Wichtigste Punkte\n"
    "Kompakte Stichpunkte (oder eine Tabelle, wenn übersichtlicher). Nur "
    "wirklich relevante Punkte, keine Vollständigkeit um jeden Preis."
)

ZUSAMMENFASSEN_SUCHANFRAGE = (
    "Hauptthema, Zusammenfassung, wichtige Punkte, Bedingungen, Beträge, "
    "Zahlen, Pflichten, Rechte, wichtige Klauseln"
)
ZUSAMMENFASSEN_SYSTEM = (
    "Du fasst Dokumente auf Basis der bereitgestellten Ausschnitte knapp "
    "zusammen. Wenn Ausschnitte aus mehreren Dokumenten vorliegen, "
    "erstelle für jedes Dokument einen eigenen Abschnitt (Dateiname als "
    "Überschrift) mit jeweils Kernergebnis + wichtigsten Punkten "
    "(Bedingungen, Beträge, Pflichten, auffällige Klauseln - nur was im "
    "Text belegt und relevant ist).\n\n"
    f"{_KUERZE_HINWEIS}\n\n{_STRUKTUR_HINWEIS}\n\n"
    f"{_QUELLENFORMAT_HINWEIS} Antworte auf Deutsch in Markdown."
)

VERGLEICHEN_SUCHANFRAGE = (
    "Laufzeit, Kündigungsfrist, Kosten, Preise, Leistungen, Pflichten, "
    "Haftung, Datenschutz, Support, Fristen, Verfügbarkeit, Bedingungen"
)
VERGLEICHEN_SYSTEM = (
    "Du vergleichst mehrere Dokumente knapp anhand der bereitgestellten "
    "Ausschnitte.\n\n"
    f"{_KUERZE_HINWEIS}\n\n"
    "Gliedere die Antwort so:\n"
    "## Kernergebnis\n"
    "1-2 Sätze: die wichtigste Erkenntnis des Vergleichs.\n\n"
    "## Vergleich\n"
    "Markdown-Tabelle (eine Zeile je Kategorie, eine Spalte je Dokument). "
    "Identifiziere eigenständig sinnvolle Kategorien anhand des "
    "tatsächlichen Inhalts (z. B. Laufzeit, Kündigungsfrist, "
    "Kosten/Preise, Leistungen, Pflichten, Haftung, Datenschutz, "
    "Support, Fristen, Verfügbarkeit o. ä.) - erzwinge keine Kategorie, "
    "zu der die Ausschnitte nichts hergeben.\n\n"
    "## Wichtigste Unterschiede\n"
    "Nur die bedeutendsten Unterschiede als kurze Stichpunkte.\n\n"
    f"{_QUELLENFORMAT_HINWEIS} Antworte auf Deutsch."
)

FRISTEN_SUCHANFRAGE = (
    "Datum, Frist, Kündigungsfrist, Vertragslaufzeit, Verlängerung, "
    "Zahlungsfrist, Termin, Stichtag, Gültigkeitsdauer"
)
FRISTEN_SYSTEM = (
    "Du extrahierst terminliche und fristbezogene Informationen knapp "
    "aus den bereitgestellten Dokumentausschnitten: Daten, Fristen, "
    "Kündigungsfristen, Vertragslaufzeiten, Verlängerungsfristen, "
    "Zahlungsfristen und andere zeitkritische Verpflichtungen.\n\n"
    "Erfinde keine Daten oder Fristen, die nicht im Text belegt sind. "
    "Wenn ein Dokument keine erkennbaren Fristen enthält, erwähne das "
    "in einem kurzen Halbsatz statt etwas zu erfinden.\n\n"
    f"{_KUERZE_HINWEIS}\n\n"
    "Gliedere die Antwort so:\n"
    "## Kernergebnis\n"
    "1 Satz: wichtigste bzw. nächste Frist.\n\n"
    "## Wichtigste Punkte\n"
    "Kompakte, wo möglich chronologisch geordnete Stichpunkte (frühester "
    "Termin zuerst) oder eine Tabelle, gruppiert nach Dokument.\n\n"
    f"{_QUELLENFORMAT_HINWEIS} Antworte auf Deutsch in Markdown."
)

RISIKEN_SUCHANFRAGE = (
    "Haftung, Ausschluss, Einschränkung, Pflicht, Vertragsstrafe, "
    "Kündigung, Sonderregelung, ungewöhnliche Bedingung, Risiko, "
    "Gewährleistung"
)
RISIKEN_SYSTEM = (
    "Du analysierst die bereitgestellten Dokumentausschnitte knapp auf "
    "potenziell wichtige, ungewöhnliche oder nachteilige Klauseln, "
    "Bedingungen, Pflichten, Haftungsregelungen, Einschränkungen oder "
    "Ausschlüsse, die besondere Aufmerksamkeit verdienen.\n\n"
    "Diese Analyse ist keine rechtliche, finanzielle oder professionelle "
    "Beratung, sondern hebt lediglich potenziell relevante Textstellen "
    "hervor - formuliere entsprechend vorsichtig (z. B. \"könnte "
    "relevant sein\", \"sollte geprüft werden\") statt abschließende "
    "Bewertungen abzugeben.\n\n"
    f"{_KUERZE_HINWEIS}\n\n"
    "Gliedere die Antwort so:\n"
    "## Kernergebnis\n"
    "1 Satz: das auffälligste Risiko.\n\n"
    "## Wichtigste Punkte\n"
    "Kompakte Stichpunkte, gruppiert nach Dokument.\n\n"
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


def rueckfrage_beantworten(analyse_ergebnis_text, dokument_ids, frage, verlauf=None):
    """Beantwortet eine Rückfrage zu einem bereits erstellten Analyseergebnis.

    Getrennt von `pdf_logik.frage_beantworten` (normaler Chat), damit der
    Rückfragen-Verlauf innerhalb der Analyse-Arbeitsfläche eigenständig
    bleibt und nicht mit normalen Chat-Konversationen vermischt wird.

    Nutzt wie der normale Chat semantische Suche über die Chunks der
    Analyse-Dokumente (Grundlage für Faktentreue + Quellen), zusätzlich
    aber das bisherige Analyseergebnis als Kontext, damit sich Fragen wie
    "Welche dieser Fristen ist am wichtigsten?" auf das Ergebnis beziehen
    können statt nur auf den rohen Dokumenttext.

    `verlauf` ist optional eine Liste bisheriger Rückfragen
    ({"frage", "antwort"}) dieser Analyse-Sitzung.
    """
    if not dokument_ids:
        raise ValueError("Es sind keine Dokumente für diese Analyse ausgewählt.")

    verlauf = verlauf or []

    zusatzkontext = "\n".join(
        f"{eintrag['frage']} {eintrag['antwort']}" for eintrag in verlauf[-2:]
    )

    anzahl_dokumente = len(dokument_ids)
    anzahl_pro_dokument = 4 if anzahl_dokumente == 1 else max(2, 8 // anzahl_dokumente)

    chunks = speicher.chunks_laden(dokument_ids)
    ausschnitte = relevante_chunks_ermitteln(
        frage,
        chunks,
        anzahl_pro_dokument=anzahl_pro_dokument,
        zusatzkontext=zusatzkontext,
    )
    relevanter_text = (
        relevanten_text_zusammenstellen(ausschnitte)
        if ausschnitte
        else "(keine passenden Ausschnitte gefunden)"
    )

    system_text = (
        "Du beantwortest Rückfragen zu einer bereits erstellten "
        "Dokumentanalyse. Nutze das folgende Analyseergebnis als Kontext "
        "und die bereitgestellten Dokumentausschnitte als "
        f"Faktengrundlage.\n\nAnalyseergebnis:\n{analyse_ergebnis_text}\n\n"
        f"{_KUERZE_HINWEIS} Nutze den bisherigen Rückfrage-Verlauf nur, "
        "um Bezüge einzuordnen, nicht als zusätzliche Wissensquelle. "
        f"{_QUELLENFORMAT_HINWEIS} Antworte auf Deutsch."
    )

    nachrichten = [{"role": "system", "content": system_text}]

    for eintrag in verlauf[-6:]:
        nachrichten.append({"role": "user", "content": eintrag["frage"]})
        nachrichten.append({"role": "assistant", "content": eintrag["antwort"]})

    nachrichten.append(
        {
            "role": "user",
            "content": f"Dokumentausschnitte:\n{relevanter_text}\n\nRückfrage: {frage}",
        }
    )

    antwort = client.responses.create(model=MODELL, input=nachrichten)

    quellen = verwendete_quellen(ausschnitte)

    return {
        "text": antwort.output_text,
        "quellen": quellen,
        "quellenhinweis": formatiere_quellenhinweis(quellen),
    }
