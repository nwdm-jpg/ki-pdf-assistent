"""Dokumentprüfung: systematische Prüfung von Dokumenten auf wichtige
Klauseln, Kosten, Fristen, Pflichten und Auffälligkeiten.

Eigenständiges Modul für den "Dokument prüfen"-Bereich von web_app.py,
analog zu `analyse.py` (Analyse & Vergleich) aufgebaut und nutzt dieselbe
gemeinsame Infrastruktur aus `ki_analyse.py` (Retrieval-/Prompt-/API-
Logik) - hier stehen nur die prüfungsspezifischen Kategorien, Vorlagen
(Presets) und Systemprompts.

Architektur für künftige Erweiterung: Jede Prüfkategorie ist ein Eintrag
in `KATEGORIEN` (Icon, Titel, Fokus-Beschreibung, Suchanfrage) und jede
Prüfvorlage ein Eintrag in `PRESETS` (Titel, zusätzlicher Fokus-Hinweis).
Eine neue Kategorie oder Vorlage hinzuzufügen bedeutet nur einen neuen
Dict-Eintrag - keine Änderung an `einzelpruefung`, `kompletter_check`
oder der UI in web_app.py nötig (die iteriert über `KATEGORIEN`/`PRESETS`).
"""

import ki_analyse


# Wie viele Chunks je Dokument in eine Einzelprüfung einfließen. Etwas
# weniger als bei Analyse & Vergleich (8), da eine Einzelprüfung
# thematisch fokussierter ist als z. B. eine Gesamt-Zusammenfassung.
CHUNKS_PRO_DOKUMENT = 6

# Für den kompletten Check wird EINE gemeinsame, breiter gefasste
# Suchanfrage genutzt (statt sechs Einzelabfragen) - das begrenzt die
# Prüfung auf eine einzige Embedding-Anfrage + einen einzigen
# Modellaufruf, statt sechs unabhängige (teure) Modellaufrufe zu
# verursachen (siehe `kompletter_check`).
CHUNKS_PRO_DOKUMENT_KOMPLETT = 12

PRUEFUNG_HINWEIS = (
    "⚠️ Diese Prüfung hebt potenziell relevante Textstellen hervor, geht "
    "dabei von keiner bestimmten Rechtsordnung aus und ersetzt keine "
    "rechtliche, steuerliche oder professionelle Beratung."
)

PRIORITAETS_LEGENDE = (
    "🔴 Besonders wichtig · 🟡 Beachten · 🟢 Unauffällig — "
    "Aufmerksamkeits-Einstufung der KI, keine objektive rechtliche Bewertung."
)

_PRIORITAETS_ANWEISUNG = (
    "Jeder Punkt beginnt mit GENAU einem Symbol - 🔴 (besonders wichtig), "
    "🟡 (beachten) oder 🟢 (unauffällig) - gefolgt von einem kurzen Titel "
    "in Fettschrift, einer knappen Erklärung (1-2 Sätze) und der Quelle. "
    "Ordne konservativ und ausschließlich anhand des Dokumentinhalts ein "
    "(🔴 nur für wirklich bedeutsame Punkte, z. B. hohe Kosten, kurze "
    "Fristen, einseitige Haftung; 🟢 für unauffällige Standardklauseln). "
    "Diese Einstufung ist eine Aufmerksamkeits-Priorität, keine "
    "rechtliche Bewertung."
)


KATEGORIEN = {
    "risiken": {
        "icon": "⚠️",
        "titel": "Risiken & kritische Stellen",
        "beschreibung": "Ungewöhnliche oder nachteilige Klauseln.",
        "fokus": (
            "potenziell wichtige, ungewöhnliche oder nachteilige "
            "Klauseln, Bedingungen, Haftungsregelungen, Einschränkungen "
            "oder Ausschlüsse"
        ),
        "suchanfrage": (
            "Haftung, Ausschluss, Einschränkung, Vertragsstrafe, "
            "Sonderregelung, ungewöhnliche Bedingung, Risiko, "
            "Gewährleistung"
        ),
    },
    "kosten": {
        "icon": "💰",
        "titel": "Kosten & finanzielle Verpflichtungen",
        "beschreibung": "Preise, Gebühren, wiederkehrende Kosten.",
        "fokus": (
            "Preise, Gebühren, wiederkehrende Kosten, Zusatzkosten, "
            "Zahlungspflichten, Preisanpassungen und sonstige "
            "finanzielle Konsequenzen"
        ),
        "suchanfrage": (
            "Preis, Kosten, Gebühr, Zahlung, Entgelt, Rechnung, "
            "Preisanpassung, Zusatzkosten, Nebenkosten, Betrag"
        ),
    },
    "fristen": {
        "icon": "📅",
        "titel": "Fristen & Laufzeiten",
        "beschreibung": "Kündigungsfristen, Laufzeiten, Termine.",
        "fokus": (
            "Kündigungsfristen, Vertragslaufzeit, automatische "
            "Verlängerungen, Zahlungsfristen, wichtige Termine und "
            "Ankündigungsfristen"
        ),
        "suchanfrage": (
            "Kündigungsfrist, Laufzeit, Verlängerung, Zahlungsfrist, "
            "Termin, Frist, Stichtag, Ankündigungsfrist"
        ),
    },
    "pflichten_eigene": {
        "icon": "📋",
        "titel": "Meine Pflichten",
        "beschreibung": "Was du als Vertragspartei tun musst.",
        "fokus": (
            "Pflichten und Verantwortlichkeiten der Nutzerin bzw. des "
            "Kunden/Vertragspartners - nicht der Gegenseite"
        ),
        "suchanfrage": (
            "Pflicht, Mitwirkungspflicht, Kunde muss, Kunde ist "
            "verpflichtet, Obliegenheit, Verantwortung"
        ),
    },
    "pflichten_gegenseite": {
        "icon": "🤝",
        "titel": "Pflichten der Gegenseite",
        "beschreibung": "Leistungen und Zusagen des Anbieters.",
        "fokus": (
            "Pflichten, Leistungen und Zusagen der anderen "
            "Vertragspartei (z. B. Anbieter, Auftragnehmer, Vermieter)"
        ),
        "suchanfrage": (
            "Anbieter verpflichtet sich, Leistungspflicht, Lieferung, "
            "Gewährleistung, Service, Support"
        ),
    },
    "unklare_regelungen": {
        "icon": "❓",
        "titel": "Unklare oder fehlende Regelungen",
        "beschreibung": "Themen, die unklar oder lückenhaft wirken.",
        "fokus": (
            "Formulierungen, die unklar, widersprüchlich oder "
            "unvollständig wirken, sowie wichtige Themen, zu denen die "
            "Ausschnitte auffällig wenig regeln. Behaupte nicht, dass "
            "gesetzlich vorgeschriebene Angaben fehlen, außer dies ist "
            "eindeutig aus dem Text ersichtlich"
        ),
        "suchanfrage": (
            "unklar, sofern nicht anders vereinbart, vorbehalten, nach "
            "billigem Ermessen, im Einzelfall, Änderungsvorbehalt"
        ),
    },
}

PRESET_STANDARD = "allgemein"

PRESETS = {
    "allgemein": {
        "titel": "Allgemeine Dokumentprüfung",
        "fokus_zusatz": "",
    },
    "vertrag": {
        "titel": "Vertrag prüfen",
        "fokus_zusatz": (
            "Es handelt sich um einen Vertrag. Achte besonders auf "
            "Laufzeit, Kündigung, Pflichten beider Vertragsparteien und "
            "Haftung."
        ),
    },
    "angebot": {
        "titel": "Angebot prüfen",
        "fokus_zusatz": (
            "Es handelt sich um ein Angebot. Achte besonders auf "
            "Preise, Leistungsumfang, Gültigkeitsdauer des Angebots und "
            "mögliche Zusatzkosten."
        ),
    },
    "rechnung": {
        "titel": "Rechnung prüfen",
        "fokus_zusatz": (
            "Es handelt sich um eine Rechnung. Achte besonders auf "
            "Rechnungsbeträge, Fälligkeiten, Zahlungsbedingungen und "
            "Auffälligkeiten bei einzelnen Positionen."
        ),
    },
}


def _preset(preset_id):
    return PRESETS.get(preset_id, PRESETS[PRESET_STANDARD])


def _kategorie_system(kategorie_id, preset_id):
    kategorie = KATEGORIEN[kategorie_id]
    preset = _preset(preset_id)

    fokus_zusatz = f"\n\n{preset['fokus_zusatz']}" if preset["fokus_zusatz"] else ""

    return (
        f"Du prüfst die bereitgestellten Dokumentausschnitte im Rahmen "
        f"einer {preset['titel'].lower()} gezielt auf: "
        f"{kategorie['fokus']}.{fokus_zusatz}\n\n"
        f"{ki_analyse.KUERZE_HINWEIS}\n\n"
        "Gliedere die Antwort so:\n"
        "## Kernergebnis\n"
        "1-2 Sätze mit dem wichtigsten Ergebnis.\n\n"
        "## Wichtigste Punkte\n"
        f"Kompakte Liste. {_PRIORITAETS_ANWEISUNG} Wenn nichts "
        "Relevantes gefunden wurde, sage das kurz statt etwas zu "
        "erfinden.\n\n"
        f"{ki_analyse.QUELLENFORMAT_HINWEIS} Antworte auf Deutsch in Markdown."
    )


def _kompletter_check_system(preset_id):
    preset = _preset(preset_id)
    fokus_zusatz = f"\n\n{preset['fokus_zusatz']}" if preset["fokus_zusatz"] else ""

    kategorien_liste = "\n".join(
        f"- {kategorie['titel']}: {kategorie['fokus']}"
        for kategorie in KATEGORIEN.values()
    )

    return (
        f"Du führst eine {preset['titel'].lower()} durch und prüfst die "
        "bereitgestellten Dokumentausschnitte kompakt auf die folgenden "
        f"Aspekte (nur wo im Text belegt, keine Vollständigkeit "
        f"erzwingen):\n{kategorien_liste}{fokus_zusatz}\n\n"
        f"{ki_analyse.KUERZE_HINWEIS}\n\n"
        "Gliedere die Antwort so:\n"
        "## Kernergebnis\n"
        "2-4 Sätze mit dem wichtigsten Gesamtergebnis.\n\n"
        "## Wichtigste Punkte\n"
        "Eine priorisierte Liste der wichtigsten Einzelpunkte über alle "
        f"Themen hinweg (nicht nach Kategorie gruppieren). "
        f"{_PRIORITAETS_ANWEISUNG}\n\n"
        f"{ki_analyse.QUELLENFORMAT_HINWEIS} Antworte auf Deutsch in Markdown."
    )


def einzelpruefung(kategorie_id, dokument_ids, preset_id=PRESET_STANDARD):
    """Führt eine einzelne Prüfkategorie über die ausgewählten Dokumente aus."""
    if kategorie_id not in KATEGORIEN:
        raise ValueError(f"Unbekannte Prüfkategorie: {kategorie_id}")

    kategorie = KATEGORIEN[kategorie_id]

    ausschnitte = ki_analyse.ausschnitte_ermitteln(
        dokument_ids, kategorie["suchanfrage"], CHUNKS_PRO_DOKUMENT
    )

    if not ausschnitte:
        raise ValueError(
            "Für die ausgewählten Dokumente konnten keine Textausschnitte "
            "gefunden werden."
        )

    system_text = _kategorie_system(kategorie_id, preset_id)

    return ki_analyse.ki_anfrage(system_text, ausschnitte)


def kompletter_check(dokument_ids, preset_id=PRESET_STANDARD):
    """Kombinierter Check über alle Kategorien in EINEM Modellaufruf.

    Bewusst nicht als sechs unabhängige `einzelpruefung`-Aufrufe
    umgesetzt: Eine gemeinsame, breiter gefasste Suchanfrage über alle
    Kategorien hinweg liefert in einer einzigen Embedding-Suche genug
    Abdeckung, und ein einziger Modellaufruf mit allen Kategorien im
    Systemprompt liefert ein kompaktes Gesamtergebnis - statt sechs
    einzelner, deutlich teurerer Modellaufrufe.
    """
    kombinierte_suchanfrage = " ".join(
        kategorie["suchanfrage"] for kategorie in KATEGORIEN.values()
    )

    ausschnitte = ki_analyse.ausschnitte_ermitteln(
        dokument_ids, kombinierte_suchanfrage, CHUNKS_PRO_DOKUMENT_KOMPLETT
    )

    if not ausschnitte:
        raise ValueError(
            "Für die ausgewählten Dokumente konnten keine Textausschnitte "
            "gefunden werden."
        )

    system_text = _kompletter_check_system(preset_id)

    return ki_analyse.ki_anfrage(system_text, ausschnitte)


def rueckfrage_beantworten(pruefung_ergebnis_text, dokument_ids, frage, verlauf=None):
    """Beantwortet eine Rückfrage zu einem bereits erstellten Prüfungsergebnis.

    Getrennt von `pdf_logik.frage_beantworten` (normaler Chat) und von
    `analyse.rueckfrage_beantworten` (Analyse & Vergleich), damit die
    drei Rückfragen-/Chat-Verläufe nicht vermischt werden - siehe
    `ki_analyse.rueckfrage_beantworten` für die gemeinsame Umsetzung.
    """
    return ki_analyse.rueckfrage_beantworten(
        pruefung_ergebnis_text,
        dokument_ids,
        frage,
        verlauf=verlauf,
        kontext_label="Prüfungsergebnis",
    )
