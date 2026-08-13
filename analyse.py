"""Analyse- und Vergleichsfunktionen für die persistente Dokumentbibliothek.

Eigenständiges Modul für den "Analyse & Vergleich"-Bereich von
web_app.py: Zusammenfassen, Vergleichen, Fristen-Extraktion und
Risikoanalyse über beliebig viele Dokumente aus der Bibliothek.

Die eigentliche Retrieval-/Prompt-/API-Logik lebt in `ki_analyse.py` und
wird auch von `pruefung.py` (Dokument prüfen) genutzt - dieses Modul
liefert nur die Analyse-spezifischen Systemprompts und Suchanfragen.

Da eine Analyse (anders als eine Chat-Frage) keine konkrete Nutzerfrage
hat, dient je Analyseart eine feste, themenbezogene "Suchanfrage" als
Grundlage für die Embedding-Suche.
"""

import ki_analyse


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

# Gemeinsame Struktur-Vorgabe für alle vier Analysearten, damit die
# Ergebnisse sich einheitlich lesen (gleiche Überschriften). Wer mehr
# Detail möchte, kann in der Rückfragen-Chat nachfragen.
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
    f"{ki_analyse.KUERZE_HINWEIS}\n\n{_STRUKTUR_HINWEIS}\n\n"
    f"{ki_analyse.QUELLENFORMAT_HINWEIS} Antworte auf Deutsch in Markdown."
)

VERGLEICHEN_SUCHANFRAGE = (
    "Laufzeit, Kündigungsfrist, Kosten, Preise, Leistungen, Pflichten, "
    "Haftung, Datenschutz, Support, Fristen, Verfügbarkeit, Bedingungen"
)
VERGLEICHEN_SYSTEM = (
    "Du vergleichst mehrere Dokumente knapp anhand der bereitgestellten "
    "Ausschnitte.\n\n"
    f"{ki_analyse.KUERZE_HINWEIS}\n\n"
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
    f"{ki_analyse.QUELLENFORMAT_HINWEIS} Antworte auf Deutsch."
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
    f"{ki_analyse.KUERZE_HINWEIS}\n\n"
    "Gliedere die Antwort so:\n"
    "## Kernergebnis\n"
    "1 Satz: wichtigste bzw. nächste Frist.\n\n"
    "## Wichtigste Punkte\n"
    "Kompakte, wo möglich chronologisch geordnete Stichpunkte (frühester "
    "Termin zuerst) oder eine Tabelle, gruppiert nach Dokument.\n\n"
    f"{ki_analyse.QUELLENFORMAT_HINWEIS} Antworte auf Deutsch in Markdown."
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
    f"{ki_analyse.KUERZE_HINWEIS}\n\n"
    "Gliedere die Antwort so:\n"
    "## Kernergebnis\n"
    "1 Satz: das auffälligste Risiko.\n\n"
    "## Wichtigste Punkte\n"
    "Kompakte Stichpunkte, gruppiert nach Dokument.\n\n"
    f"{ki_analyse.QUELLENFORMAT_HINWEIS} Antworte auf Deutsch in Markdown."
)


def _analyse_durchfuehren(system_text, suchanfrage, dokument_ids):
    ausschnitte = ki_analyse.ausschnitte_ermitteln(
        dokument_ids, suchanfrage, CHUNKS_PRO_DOKUMENT
    )

    if not ausschnitte:
        raise ValueError(
            "Für die ausgewählten Dokumente konnten keine Textausschnitte "
            "gefunden werden."
        )

    return ki_analyse.ki_anfrage(system_text, ausschnitte)


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

    Getrennt von `pdf_logik.frage_beantworten` (normaler Chat) und von
    `pruefung.rueckfrage_beantworten` (Dokument prüfen), damit die drei
    Rückfragen-/Chat-Verläufe nicht vermischt werden - siehe
    `ki_analyse.rueckfrage_beantworten` für die gemeinsame Umsetzung.
    """
    return ki_analyse.rueckfrage_beantworten(
        analyse_ergebnis_text,
        dokument_ids,
        frage,
        verlauf=verlauf,
        kontext_label="Analyseergebnis",
    )
