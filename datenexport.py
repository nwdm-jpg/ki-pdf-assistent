"""Erstellt den strukturierten ZIP-Datenexport für einen angemeldeten Benutzer.

Jede Datenquelle läuft ausschließlich über bereits benutzer-scoped
`speicher.py`-Funktionen (`benutzer_konto_daten`, `dokumente_laden`,
`chat_liste`, `chat_laden`, `dokument_datei_lesen`) - dieses Modul
konstruiert selbst NIE eine SQL-Abfrage oder einen Dateipfad aus einer
`benutzer_id`, sondern reicht sie ausschließlich an diese bereits
Eigentümerschaft-geprüften Funktionen durch. Enthält bewusst NIE:
Passwort-Hash, Verifizierungs-/Reset-Token(-Hashes), Rohdaten anderer
Benutzer.
"""

import io
import json
import zipfile
from pathlib import PureWindowsPath

import speicher


def _sicherer_zip_dateiname(dateiname, dokument_id):
    """Leitet einen sicheren ZIP-Eintragsnamen aus dem (nutzergesteuerten,
    NICHT sanitisierten) Original-Dateinamen eines Uploads ab.

    `dokumente.dateiname` stammt unverändert vom Browser (`datei.name` in
    `web_app.py`s Uploader) - nirgends sonst in der App wird er als
    Datei-/Archivpfad verwendet (die tatsächliche Datei auf der Platte
    heißt immer `<hash>.<dateityp>`, siehe `_benutzer_dokumente_ordner`),
    dieser Export ist die einzige Stelle. Ein bösartiger oder unglücklich
    benannter Upload wie "../../evil.txt" oder "..\\..\\evil.txt" würde
    sonst als ZIP-Eintragspfad mit Verzeichnis-Traversal landen (Zip-Slip)
    und beim Entpacken außerhalb des Export-Ordners schreiben können.
    `PureWindowsPath(...).name` entfernt sowohl "/"- als auch
    "\\"-Pfadsegmente sowie einen eventuellen Laufwerksbuchstaben und
    liefert nur die letzte Pfadkomponente - unabhängig davon, auf welchem
    Betriebssystem Clevoriq selbst läuft. Ein Original-Dateiname, der
    NUR aus Pfadtrennzeichen oder Punkten bestand (z. B. "..") bekommt
    einen Fallback über die Dokument-ID: `PurePath.name` liefert für
    einen reinen ".."-Namen nämlich unverändert ".." zurück (es wird kein
    Traversal-Segment aufgelöst, ".." ist dort einfach die "letzte
    Komponente" eines Ein-Segment-Pfades) - als ZIP-Eintragsname wäre das
    weiterhin ein Traversal-Segment, deshalb hier explizit ausgeschlossen
    statt sich allein auf `.name` zu verlassen.
    """
    sicher = PureWindowsPath(dateiname or "").name.strip()
    return sicher if sicher not in ("", ".", "..") else f"dokument-{dokument_id}"


def zip_erstellen(benutzer_id):
    """Baut den vollständigen Datenexport dieses Benutzers als ZIP-Bytes.

    Struktur:
        Clevoriq-Datenexport/
            konto.json
            dokumente.json
            chats.json
            nachrichten.json
            dokumente/<dateiname>
    """
    konto = speicher.benutzer_konto_daten(benutzer_id)
    dokumente = speicher.dokumente_laden(benutzer_id)
    chats_liste = speicher.chat_liste(benutzer_id)

    dokumente_json = [
        {
            "id": d["id"],
            "dateiname": d["dateiname"],
            "dateityp": d.get("dateityp"),
            "einheit_typ": d.get("einheit_typ"),
            "einheiten_anzahl": d.get("seitenzahl"),
            "groesse_bytes": d.get("groesse_bytes"),
            "hochgeladen_am": d.get("hochgeladen_am"),
        }
        for d in dokumente
    ]

    chats_json = []
    nachrichten_json = []

    for eintrag in chats_liste:
        chat = speicher.chat_laden(eintrag["id"], benutzer_id)

        if not chat:
            continue

        chats_json.append(
            {
                "id": chat["id"],
                "titel": chat["titel"],
                "erstellt_am": chat["erstellt_am"],
                "aktualisiert_am": chat["aktualisiert_am"],
                "dokument_ids": chat["dokument_ids"],
            }
        )

        for nachricht in chat["nachrichten"]:
            nachrichten_json.append(
                {
                    "chat_id": chat["id"],
                    "frage": nachricht["frage"],
                    "antwort": nachricht["antwort"],
                    "quellen": nachricht["quellen"],
                }
            )

    puffer = io.BytesIO()
    basis = "Clevoriq-Datenexport"

    with zipfile.ZipFile(puffer, "w", zipfile.ZIP_DEFLATED) as zip_datei:
        zip_datei.writestr(f"{basis}/konto.json", json.dumps(konto, ensure_ascii=False, indent=2))
        zip_datei.writestr(
            f"{basis}/dokumente.json", json.dumps(dokumente_json, ensure_ascii=False, indent=2)
        )
        zip_datei.writestr(f"{basis}/chats.json", json.dumps(chats_json, ensure_ascii=False, indent=2))
        zip_datei.writestr(
            f"{basis}/nachrichten.json", json.dumps(nachrichten_json, ensure_ascii=False, indent=2)
        )

        # Originaldateien - über `dokument_datei_lesen` liest dieses Modul
        # nie selbst vom Dateisystem, sondern bekommt Bytes nur für
        # tatsächlich diesem Benutzer gehörende Dokumente zurück (oder
        # None, dann wird die Datei im Export übersprungen statt den
        # Export abzubrechen, z. B. falls eine Originaldatei fehlt).
        verwendete_namen = set()

        for d in dokumente:
            inhalt = speicher.dokument_datei_lesen(d["id"], benutzer_id)

            if inhalt is None:
                continue

            name = _sicherer_zip_dateiname(d["dateiname"], d["id"])

            if name in verwendete_namen:
                name = f"{d['id']}-{name}"

            verwendete_namen.add(name)
            zip_datei.writestr(f"{basis}/dokumente/{name}", inhalt)

    return puffer.getvalue()
