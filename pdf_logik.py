"""Gemeinsame Logik für PDF-Verarbeitung, dokumentübergreifendes Retrieval
und KI-Anfragen mit Chatverlauf.

Wird sowohl von app.py (CLI, ein Dokument) als auch von web_app.py
(Streamlit, mehrere Dokumente) verwendet.

Ein Seiteneintrag ist ein Dict der Form
{"dateiname": str, "seitennummer": int, "text": str}. Für
dokumentübergreifende Suche werden die Seiteneinträge mehrerer Dokumente
einfach zu einer flachen Liste zusammengeführt.
"""

import re

from openai import OpenAI


STOPPWOERTER = {
    "der",
    "die",
    "das",
    "ein",
    "eine",
    "und",
    "oder",
    "ist",
    "sind",
    "im",
    "in",
    "am",
    "an",
    "auf",
    "zu",
    "zur",
    "zum",
    "mit",
    "von",
    "für",
    "welche",
    "was",
    "wie",
}

MODELL = "gpt-5-mini"

# Der OPENAI_API_KEY wird von OpenAI() automatisch aus der Umgebung gelesen.
client = OpenAI()


def pdf_seiten_extrahieren(reader, dateiname):
    """Extrahiert den Text aller Seiten eines PdfReader.

    Gibt eine Liste von Seiteneinträgen zurück (nur Seiten mit
    extrahierbarem Text).
    """
    seiten = []

    for nummer, seite in enumerate(reader.pages, start=1):
        text = seite.extract_text()

        if text:
            seiten.append(
                {"dateiname": dateiname, "seitennummer": nummer, "text": text}
            )

    return seiten


def relevante_seiten_ermitteln(frage, seiten, anzahl=3, zusatzkontext=""):
    """Bewertet Seiteneinträge anhand der Wortüberschneidung mit der Frage
    und liefert je Dokument die `anzahl` besten Seiten zurück.

    Die Auswahl erfolgt bewusst pro Dokument (nicht als ein gemeinsamer
    Top-N über alle Seiten hinweg): Sonst könnte ein bereits besprochenes
    Dokument bei einer Rückfrage wie "Und wie ist das im zweiten
    Vertrag?" allein durch Kontext-Überschneidung alle Plätze belegen und
    das eigentlich gemeinte zweite Dokument komplett verdrängen. So
    bleiben alle hochgeladenen Dokumente im Blick der KI, die dank
    Chatverlauf (siehe `frage_beantworten`) den Bezug "zweiter Vertrag"
    selbst auflösen kann.

    `zusatzkontext` (z. B. der letzte Chatverlauf) fließt zusätzlich in
    die Bewertung ein, damit auch inhaltsarme Rückfragen noch die
    passenden Seiten finden.

    Gibt eine Liste von Seiteneinträgen zurück, gruppiert nach Dokument
    (in der Reihenfolge ihres ersten Auftretens in `seiten`) und
    innerhalb jedes Dokuments absteigend nach Trefferzahl sortiert.
    """
    suchtext = f"{zusatzkontext}\n{frage}" if zusatzkontext else frage

    frage_woerter = {
        wort
        for wort in re.findall(r"\w+", suchtext.lower())
        if wort not in STOPPWOERTER
    }

    seiten_je_dokument = {}

    for eintrag in seiten:
        seiten_je_dokument.setdefault(eintrag["dateiname"], []).append(eintrag)

    ergebnis = []

    for dokument_seiten in seiten_je_dokument.values():
        bewertete_seiten = []

        for eintrag in dokument_seiten:
            seiten_woerter = set(re.findall(r"\w+", eintrag["text"].lower()))

            treffer = len(frage_woerter & seiten_woerter)

            bewertete_seiten.append((treffer, eintrag))

        bewertete_seiten.sort(key=lambda paar: paar[0], reverse=True)

        ergebnis.extend(eintrag for _, eintrag in bewertete_seiten[:anzahl])

    return ergebnis


def relevanten_text_zusammenstellen(beste_seiten):
    """Baut aus den besten Seiten den Text für den KI-Prompt zusammen."""
    return "\n\n".join(
        f"--- {eintrag['dateiname']}, Seite {eintrag['seitennummer']} ---\n"
        f"{eintrag['text']}"
        for eintrag in beste_seiten
    )


def verwendete_quellen(beste_seiten):
    """Extrahiert (dateiname, seitennummer)-Paare aus den besten Seiten."""
    return [
        (eintrag["dateiname"], eintrag["seitennummer"]) for eintrag in beste_seiten
    ]


def formatiere_quellenhinweis(quellen):
    """Formatiert (dateiname, seitennummer)-Paare als lesbaren Quellenhinweis.

    Gruppiert nach Dateiname, entfernt Duplikate und sortiert Dateinamen
    sowie Seitenzahlen aufsteigend. Verwendet je Dokument die Singular-
    ("Seite 3") bzw. Pluralform ("Seiten 3, 7"). Gibt "" zurück, wenn
    keine Quellen übergeben wurden.
    """
    seiten_je_datei = {}

    for dateiname, seitennummer in quellen:
        seiten_je_datei.setdefault(dateiname, set()).add(seitennummer)

    if not seiten_je_datei:
        return ""

    teile = []

    for dateiname in sorted(seiten_je_datei):
        seitenzahlen = sorted(seiten_je_datei[dateiname])

        if len(seitenzahlen) == 1:
            teile.append(f"{dateiname} (Seite {seitenzahlen[0]})")
        else:
            teile.append(
                f"{dateiname} (Seiten "
                + ", ".join(str(s) for s in seitenzahlen)
                + ")"
            )

    return "Quellen: " + "; ".join(teile)


def frage_beantworten(frage, relevanter_text, verlauf=None):
    """Stellt die Frage zusammen mit dem relevanten PDF-Text an die KI.

    `verlauf` ist optional eine Liste bisheriger Chat-Einträge
    ({"frage": ..., "antwort": ...}) aus dem aktuellen Gespräch, damit
    Rückfragen ohne Wiederholung des Themas verstanden werden. Der
    Verlauf dient dabei nur der Einordnung von Bezügen, nicht als
    zusätzliche Wissensquelle.
    """
    system_text = (
        "Beantworte die Frage ausschließlich anhand der bereitgestellten "
        "PDF-Seiten. Wenn die Antwort nicht im Text steht, sage das klar."
    )

    if verlauf:
        system_text += (
            " Nutze den bisherigen Chatverlauf ausschließlich, um Bezüge "
            "und Rückfragen (z. B. 'im zweiten Vertrag') richtig "
            "einzuordnen — nicht als zusätzliche Wissensquelle."
        )

    nachrichten = [{"role": "system", "content": system_text}]

    for eintrag in verlauf or []:
        nachrichten.append({"role": "user", "content": eintrag["frage"]})
        nachrichten.append({"role": "assistant", "content": eintrag["antwort"]})

    nachrichten.append(
        {
            "role": "user",
            "content": (
                f"Relevante PDF-Seiten:\n{relevanter_text}\n\n"
                f"Frage: {frage}"
            ),
        }
    )

    antwort = client.responses.create(model=MODELL, input=nachrichten)

    return antwort.output_text
