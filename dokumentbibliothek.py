"""Filter-, Sortier- und Konstanten-Logik für die Dokumentbibliothek.

Reine, von Streamlit unabhängige Hilfsfunktionen (`dokumente_filtern`,
`dokumente_sortieren`) - leicht isoliert testbar, ohne UI-Abhängigkeit.
Wird vom "📚 Dokumentenbibliothek"-Bereich in web_app.py verwendet,
damit dort keine Filter-/Sortierlogik direkt mit Streamlit-Aufrufen
vermischt wird.

Wichtig: Diese Funktionen verändern nie die übergebenen Dokumente oder
irgendwelche Auswahl-/Session-Zustände - Suche, Sortierung und Filter
sind rein präsentational und dürfen niemals gespeicherte Auswahlen
(Chat- oder Analyse-/Prüfungsauswahl) beeinflussen.
"""

from datetime import datetime, timedelta

from quellen import EINHEIT_WOERTER


SORTIERUNG_NEUESTE = "Neueste zuerst"
SORTIERUNG_AELTESTE = "Älteste zuerst"
SORTIERUNG_AZ = "A–Z"
SORTIERUNG_ZA = "Z–A"
SORTIERUNG_MEISTE_SEITEN = "Meiste Einheiten zuerst"
SORTIERUNG_WENIGSTE_SEITEN = "Wenigste Einheiten zuerst"
SORTIERUNG_GROESSTE = "Größte zuerst"
SORTIERUNG_KLEINSTE = "Kleinste zuerst"

SORTIERUNGEN = [
    SORTIERUNG_NEUESTE,
    SORTIERUNG_AELTESTE,
    SORTIERUNG_AZ,
    SORTIERUNG_ZA,
    SORTIERUNG_MEISTE_SEITEN,
    SORTIERUNG_WENIGSTE_SEITEN,
    SORTIERUNG_GROESSTE,
    SORTIERUNG_KLEINSTE,
]

DATEITYP_ALLE = "Alle Typen"

# Icon + Anzeigename je unterstützter Dateiendung, für Badges in der
# Bibliotheksansicht. Unbekannte/künftige Typen fallen auf ein
# generisches Icon + die Endung in Großbuchstaben zurück (siehe
# `dateityp_anzeige`).
DATEITYP_ANZEIGE = {
    "pdf": {"icon": "📄", "label": "PDF"},
    "docx": {"icon": "📝", "label": "Word"},
    "txt": {"icon": "📃", "label": "Text"},
    "md": {"icon": "📃", "label": "Markdown"},
    "csv": {"icon": "📊", "label": "CSV"},
    "xlsx": {"icon": "📊", "label": "Excel"},
    "pptx": {"icon": "📽️", "label": "PowerPoint"},
}


def dateityp_anzeige(dateityp):
    """Icon + Anzeigename für einen Dateityp, z. B. "📝 Word"."""
    eintrag = DATEITYP_ANZEIGE.get(
        dateityp, {"icon": "📄", "label": (dateityp or "?").upper()}
    )
    return f"{eintrag['icon']} {eintrag['label']}"


def dateitypen_optionen(dokumente):
    """Filteroptionen für den Dateityp: 'Alle Typen' + tatsächlich vorhandene Typen."""
    vorhandene = sorted({dokument.get("dateityp", "pdf") for dokument in dokumente})
    return [DATEITYP_ALLE] + vorhandene


def einheiten_text(dokument):
    """Formatiert die Einheitenanzahl passend zum Dokumenttyp, z. B. "12 Seiten", "5 Folien"."""
    anzahl = dokument["seitenzahl"]
    singular, plural = EINHEIT_WOERTER.get(
        dokument.get("einheit_typ", "seite"), EINHEIT_WOERTER["seite"]
    )
    return f"{anzahl} {singular if anzahl == 1 else plural}"


def groesse_text(dokument):
    """Menschlich lesbare Dateigröße, oder None wenn unbekannt (z. B. Alt-Dokumente)."""
    groesse = dokument.get("groesse_bytes")

    if not groesse:
        return None
    if groesse < 1024:
        return f"{groesse} B"
    if groesse < 1024 * 1024:
        return f"{groesse / 1024:.0f} KB"

    return f"{groesse / (1024 * 1024):.1f} MB"


DATUMSFILTER_ALLE = "Alle"
DATUMSFILTER_HEUTE = "Heute"
DATUMSFILTER_7_TAGE = "Letzte 7 Tage"
DATUMSFILTER_30_TAGE = "Letzte 30 Tage"
DATUMSFILTER_BENUTZERDEFINIERT = "Benutzerdefiniert"

DATUMSFILTER = [
    DATUMSFILTER_ALLE,
    DATUMSFILTER_HEUTE,
    DATUMSFILTER_7_TAGE,
    DATUMSFILTER_30_TAGE,
    DATUMSFILTER_BENUTZERDEFINIERT,
]

def dokumente_filtern(
    dokumente,
    suchbegriff="",
    datumsfilter=DATUMSFILTER_ALLE,
    benutzerdefiniert_von=None,
    benutzerdefiniert_bis=None,
    dateityp_filter=DATEITYP_ALLE,
    jetzt=None,
):
    """Filtert Dokumente nach Suchbegriff, Upload-Datum und Dateityp.

    Reine Funktion: verändert weder `dokumente` noch irgendeinen
    Auswahl-/Session-Zustand, sondern gibt nur eine gefilterte Kopie der
    Liste zurück. Der Suchbegriff wird case-insensitiv auf den
    Dateinamen angewendet.
    """
    jetzt = jetzt or datetime.now()
    suchbegriff = suchbegriff.strip().lower()

    ergebnis = []

    for dokument in dokumente:
        if suchbegriff and suchbegriff not in dokument["dateiname"].lower():
            continue

        if datumsfilter != DATUMSFILTER_ALLE:
            hochgeladen_am = datetime.fromisoformat(dokument["hochgeladen_am"])

            if datumsfilter == DATUMSFILTER_HEUTE and hochgeladen_am.date() != jetzt.date():
                continue
            if datumsfilter == DATUMSFILTER_7_TAGE and hochgeladen_am < jetzt - timedelta(days=7):
                continue
            if datumsfilter == DATUMSFILTER_30_TAGE and hochgeladen_am < jetzt - timedelta(days=30):
                continue
            if datumsfilter == DATUMSFILTER_BENUTZERDEFINIERT:
                hochgeladen_am_datum = hochgeladen_am.date()
                if benutzerdefiniert_von and hochgeladen_am_datum < benutzerdefiniert_von:
                    continue
                if benutzerdefiniert_bis and hochgeladen_am_datum > benutzerdefiniert_bis:
                    continue

        if (
            dateityp_filter != DATEITYP_ALLE
            and dokument.get("dateityp", "pdf") != dateityp_filter
        ):
            continue

        ergebnis.append(dokument)

    return ergebnis


def dokumente_sortieren(dokumente, sortierung=SORTIERUNG_NEUESTE):
    """Sortiert Dokumente; reine Funktion, gibt eine neue (sortierte) Liste zurück."""
    if sortierung == SORTIERUNG_NEUESTE:
        return sorted(dokumente, key=lambda d: d["hochgeladen_am"], reverse=True)
    if sortierung == SORTIERUNG_AELTESTE:
        return sorted(dokumente, key=lambda d: d["hochgeladen_am"])
    if sortierung == SORTIERUNG_AZ:
        return sorted(dokumente, key=lambda d: d["dateiname"].lower())
    if sortierung == SORTIERUNG_ZA:
        return sorted(dokumente, key=lambda d: d["dateiname"].lower(), reverse=True)
    if sortierung == SORTIERUNG_MEISTE_SEITEN:
        return sorted(dokumente, key=lambda d: d["seitenzahl"], reverse=True)
    if sortierung == SORTIERUNG_WENIGSTE_SEITEN:
        return sorted(dokumente, key=lambda d: d["seitenzahl"])
    if sortierung == SORTIERUNG_GROESSTE:
        return sorted(dokumente, key=lambda d: d.get("groesse_bytes") or 0, reverse=True)
    if sortierung == SORTIERUNG_KLEINSTE:
        return sorted(dokumente, key=lambda d: d.get("groesse_bytes") or 0)

    return list(dokumente)
