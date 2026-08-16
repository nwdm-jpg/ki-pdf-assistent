"""Gemeinsame Bausteine für KI-gestützte Analysen über die Dokumentbibliothek.

Wird sowohl von `analyse.py` (Analyse & Vergleich) als auch von
`pruefung.py` (Dokument prüfen) genutzt, damit Retrieval- und
Prompt-Aufbau-Logik nicht doppelt existiert. Baut auf `speicher.py`
(gespeicherte Chunks/Embeddings), `retrieval.py` (semantische Suche),
`pdf_logik.py` (OpenAI-Client) und `quellen.py` (formatgerechte
Quellenangaben) auf - hier wird nichts davon dupliziert, nur zu einem
gemeinsamen Ablauf zusammengesetzt:

1. `ausschnitte_ermitteln` - lädt gespeicherte Chunks der ausgewählten
   Dokumente und wählt per Embedding-Ähnlichkeit die relevantesten aus
   (begrenzt Kontextgröße/Kosten, statt ganze PDFs zu senden).
2. `ki_anfrage` - baut daraus (+ optional Frage/Rückfrage-Verlauf) die
   Nachrichtenliste für die OpenAI Responses API und liefert
   Antworttext + Quellen zurück.
3. `rueckfrage_beantworten` - generische Rückfragen-Funktion für ein
   bereits erstelltes Ergebnis (Analyse oder Prüfung); hält den
   Rückfragen-Verlauf als reinen Funktionsparameter, damit die
   aufrufenden Module ihn in getrennten, unabhängigen Session-State-Keys
   halten können.
"""

import speicher
from pdf_logik import MODELL, client
from quellen import (
    formatiere_quellenhinweis,
    relevanten_text_zusammenstellen,
    verwendete_quellen,
)
from retrieval import relevante_chunks_ermitteln


# Gemeinsame Kürze-Vorgabe für alle KI-Analysen (Zusammenfassen,
# Vergleichen, Fristen, Risiken, Dokumentprüfung, Rückfragen), damit sich
# alle Ergebnisse gleich knapp und auf einen Blick erfassbar lesen.
KUERZE_HINWEIS = (
    "Antworte sehr knapp: keine lange Einleitung, keine Wiederholungen, "
    "keine ausschweifende oder juristisch anmutende Sprache. Nutze kurze "
    "Stichpunkte und, wo sinnvoll, kompakte Tabellen statt Fließtext. Das "
    "Ergebnis soll ohne langes Scrollen erfassbar sein - Details kann der "
    "Nutzer in einer Rückfrage erfragen."
)

QUELLENFORMAT_HINWEIS = (
    'Belege wichtige Aussagen direkt im Text mit der Quelle im Format '
    '"(Quelle: Dateiname, Seite X)", basierend auf den Kennzeichnungen '
    "der Dokumentausschnitte. Nutze ausschließlich die bereitgestellten "
    "Ausschnitte als Informationsquelle und erfinde keine Informationen, "
    "Daten oder Quellenangaben."
)


def ausschnitte_ermitteln(dokument_ids, suchanfrage, benutzer_id, anzahl_pro_dokument=8, zusatzkontext=""):
    """Lädt gespeicherte Chunks der Dokumente und wählt die relevantesten aus.

    `benutzer_id` wird unverändert an `speicher.chunks_laden` durchgereicht,
    das die eigentliche Eigentümerprüfung per SQL-Join übernimmt (siehe
    dort) - IDs in `dokument_ids`, die nicht dem angegebenen Benutzer
    gehören, liefern schlicht keine Chunks, unabhängig davon, woher die
    Liste stammt. Wirft `ValueError`, wenn keine Dokumente ausgewählt
    wurden. Liefert andernfalls eine (ggf. leere) Liste von
    Chunk-Einträgen - leer ist hier kein Fehler, sondern kann bedeuten,
    dass zur Suchanfrage nichts Passendes gefunden wurde (relevant z. B.
    bei Rückfragen).
    """
    if not dokument_ids:
        raise ValueError("Es wurden keine Dokumente ausgewählt.")

    chunks = speicher.chunks_laden(dokument_ids, benutzer_id)

    return relevante_chunks_ermitteln(
        suchanfrage,
        chunks,
        anzahl_pro_dokument=anzahl_pro_dokument,
        zusatzkontext=zusatzkontext,
    )


def ki_anfrage(system_text, ausschnitte, frage=None, verlauf=None):
    """Stellt eine KI-Anfrage mit Dokumentausschnitten zusammen und ruft das Modell auf.

    `frage` ist optional eine konkrete Nutzerfrage (z. B. bei Rückfragen);
    ohne `frage` wird nur auf Basis der Ausschnitte geantwortet (z. B.
    Zusammenfassen/Vergleichen/Prüfen). `verlauf` ist optional eine Liste
    bisheriger Runden ({"frage", "antwort"}), die als abwechselnde
    user/assistant-Nachrichten vor der aktuellen Anfrage eingefügt werden.

    Gibt {"text", "quellen", "quellenhinweis"} zurück.
    """
    relevanter_text = (
        relevanten_text_zusammenstellen(ausschnitte)
        if ausschnitte
        else "(keine passenden Ausschnitte gefunden)"
    )

    nachrichten = [{"role": "system", "content": system_text}]

    for eintrag in (verlauf or [])[-6:]:
        nachrichten.append({"role": "user", "content": eintrag["frage"]})
        nachrichten.append({"role": "assistant", "content": eintrag["antwort"]})

    inhalt = f"Dokumentausschnitte:\n{relevanter_text}"

    if frage:
        inhalt += f"\n\nFrage: {frage}"

    nachrichten.append({"role": "user", "content": inhalt})

    antwort = client.responses.create(model=MODELL, input=nachrichten)

    quellen = verwendete_quellen(ausschnitte)

    return {
        "text": antwort.output_text,
        "quellen": quellen,
        "quellenhinweis": formatiere_quellenhinweis(quellen),
    }


def rueckfrage_beantworten(ergebnis_text, dokument_ids, benutzer_id, frage, verlauf=None, kontext_label="Ergebnis"):
    """Beantwortet eine Rückfrage zu einem bereits erstellten Ergebnis.

    Generisch nutzbar für Analyse & Vergleich UND Dokument prüfen (siehe
    `analyse.rueckfrage_beantworten` bzw. `pruefung.rueckfrage_beantworten`,
    die diese Funktion mit passendem `kontext_label` aufrufen). Der
    Rückfragen-Verlauf bleibt dadurch pro Bereich getrennt, da jeder
    Aufrufer seinen eigenen `verlauf` verwaltet statt eines gemeinsamen
    globalen Zustands.

    Nutzt wie eine normale Chat-Frage semantische Suche über die Chunks
    der ausgewählten Dokumente (Grundlage für Faktentreue + Quellen),
    zusätzlich aber das bisherige Ergebnis als Kontext, damit sich Fragen
    wie "Warum ist dieser Punkt rot?" darauf beziehen können.
    """
    if not dokument_ids:
        raise ValueError("Es sind keine Dokumente ausgewählt.")

    verlauf = verlauf or []

    zusatzkontext = "\n".join(
        f"{eintrag['frage']} {eintrag['antwort']}" for eintrag in verlauf[-2:]
    )

    anzahl_dokumente = len(dokument_ids)
    anzahl_pro_dokument = 4 if anzahl_dokumente == 1 else max(2, 8 // anzahl_dokumente)

    ausschnitte = ausschnitte_ermitteln(
        dokument_ids, frage, benutzer_id, anzahl_pro_dokument, zusatzkontext
    )

    system_text = (
        f"Du beantwortest Rückfragen zu einem bereits erstellten "
        f"{kontext_label}. Nutze das folgende {kontext_label} als Kontext "
        "und die bereitgestellten Dokumentausschnitte als "
        f"Faktengrundlage.\n\n{kontext_label}:\n{ergebnis_text}\n\n"
        f"{KUERZE_HINWEIS} Nutze den bisherigen Rückfrage-Verlauf nur, um "
        f"Bezüge einzuordnen, nicht als zusätzliche Wissensquelle. "
        f"{QUELLENFORMAT_HINWEIS} Antworte auf Deutsch."
    )

    return ki_anfrage(system_text, ausschnitte, frage=frage, verlauf=verlauf)
