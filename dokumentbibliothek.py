"""Filter-, Sortier- und Konstanten-Logik für die Dokumentbibliothek.

Reine, von Streamlit unabhängige Hilfsfunktionen (`dokumente_filtern`,
`dokumente_sortieren`) - leicht isoliert testbar, ohne UI-Abhängigkeit.
Wird ausschließlich vom Sidebar-Bibliotheksbereich in web_app.py
verwendet, damit dort keine Filter-/Sortierlogik direkt vermischt mit
Streamlit-Aufrufen entsteht.

Wichtig: Diese Funktionen verändern nie die übergebenen Dokumente oder
irgendwelche Auswahl-/Session-Zustände - Suche, Sortierung und Filter
sind rein präsentational und dürfen niemals gespeicherte Auswahlen
(Chat- oder Analyse-/Prüfungsauswahl) beeinflussen.
"""

from datetime import datetime, timedelta


SORTIERUNG_NEUESTE = "Neueste zuerst"
SORTIERUNG_AELTESTE = "Älteste zuerst"
SORTIERUNG_AZ = "A–Z"
SORTIERUNG_ZA = "Z–A"
SORTIERUNG_MEISTE_SEITEN = "Meiste Seiten zuerst"
SORTIERUNG_WENIGSTE_SEITEN = "Wenigste Seiten zuerst"

SORTIERUNGEN = [
    SORTIERUNG_NEUESTE,
    SORTIERUNG_AELTESTE,
    SORTIERUNG_AZ,
    SORTIERUNG_ZA,
    SORTIERUNG_MEISTE_SEITEN,
    SORTIERUNG_WENIGSTE_SEITEN,
]

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

AUSWAHLFILTER_ALLE = "Alle Dokumente"
AUSWAHLFILTER_AKTIV = "Aktive Dokumente"
AUSWAHLFILTER_INAKTIV = "Nicht ausgewählte Dokumente"

AUSWAHLFILTER = [AUSWAHLFILTER_ALLE, AUSWAHLFILTER_AKTIV, AUSWAHLFILTER_INAKTIV]


def dokumente_filtern(
    dokumente,
    suchbegriff="",
    datumsfilter=DATUMSFILTER_ALLE,
    benutzerdefiniert_von=None,
    benutzerdefiniert_bis=None,
    aktive_ids=None,
    auswahlfilter=AUSWAHLFILTER_ALLE,
    jetzt=None,
):
    """Filtert Dokumente nach Suchbegriff, Upload-Datum und Auswahlstatus.

    Reine Funktion: verändert weder `dokumente` noch irgendeinen
    Auswahl-/Session-Zustand, sondern gibt nur eine gefilterte Kopie der
    Liste zurück. Der Suchbegriff wird case-insensitiv auf den
    Dateinamen angewendet.

    `aktive_ids` wird nur für den Auswahlfilter benötigt; ist es `None`
    (z. B. außerhalb des Chat-Bereichs, wo es keine Sidebar-Auswahl
    gibt), wird der Auswahlfilter ignoriert, selbst wenn `auswahlfilter`
    gesetzt ist.
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

        if aktive_ids is not None and auswahlfilter != AUSWAHLFILTER_ALLE:
            ist_aktiv = dokument["id"] in aktive_ids

            if auswahlfilter == AUSWAHLFILTER_AKTIV and not ist_aktiv:
                continue
            if auswahlfilter == AUSWAHLFILTER_INAKTIV and ist_aktiv:
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

    return list(dokumente)
