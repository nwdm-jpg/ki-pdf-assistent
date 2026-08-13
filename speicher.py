"""Persistenz für Dokumentbibliothek und Chats (SQLite + lokale Dateikopien).

Alle Daten liegen im projektlokalen Ordner `app_daten/` (nicht Teil des
Git-Repos): `app_daten/bibliothek.db` für Metadaten, Chunks, Embeddings,
Chats und Nachrichten; `app_daten/pdfs/` für die Originaldateien
(benannt nach ihrem Datei-Hash + tatsächlicher Endung, zur
Duplikaterkennung) - der Ordnername ist historisch (aus der PDF-only-
Zeit) geblieben, enthält inzwischen aber Originaldateien aller
unterstützten Formate.
"""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import numpy as np


APP_DATEN_ORDNER = Path(__file__).resolve().parent / "app_daten"
PDF_ORDNER = APP_DATEN_ORDNER / "pdfs"
DB_PFAD = APP_DATEN_ORDNER / "bibliothek.db"

STANDARD_CHAT_TITEL = "Neuer Chat"


@contextmanager
def _verbindung():
    APP_DATEN_ORDNER.mkdir(exist_ok=True)
    PDF_ORDNER.mkdir(exist_ok=True)

    conn = sqlite3.connect(DB_PFAD)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _spalten_ergaenzen(conn, tabelle, spalten):
    """Fügt fehlender Tabelle fehlende Spalten additiv hinzu (Migration).

    Prüft je Spalte per `PRAGMA table_info`, ob sie bereits existiert,
    bevor `ALTER TABLE ... ADD COLUMN` ausgeführt wird. Rein additiv und
    beliebig oft ausführbar - bestehende Datenbanken (inkl. aller
    vorhandenen Zeilen) bleiben beim Start einer neueren App-Version
    unangetastet, es werden nur fehlende Spalten mit Default-Werten
    ergänzt.
    """
    vorhandene_spalten = {
        zeile["name"] for zeile in conn.execute(f"PRAGMA table_info({tabelle})")
    }

    for spalte, definition in spalten:
        if spalte not in vorhandene_spalten:
            conn.execute(f"ALTER TABLE {tabelle} ADD COLUMN {spalte} {definition}")


def datenbank_initialisieren():
    """Legt die benötigten Tabellen an bzw. ergänzt fehlende Spalten."""
    with _verbindung() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS dokumente (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dateiname TEXT NOT NULL,
                hash TEXT NOT NULL UNIQUE,
                seitenzahl INTEGER NOT NULL,
                hochgeladen_am TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dokument_id INTEGER NOT NULL REFERENCES dokumente(id) ON DELETE CASCADE,
                seitennummer INTEGER NOT NULL,
                text TEXT NOT NULL,
                embedding BLOB NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                titel TEXT NOT NULL,
                erstellt_am TEXT NOT NULL,
                aktualisiert_am TEXT NOT NULL,
                dokument_ids TEXT NOT NULL DEFAULT '[]'
            );

            CREATE TABLE IF NOT EXISTS nachrichten (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                frage TEXT NOT NULL,
                antwort TEXT NOT NULL,
                quellen TEXT NOT NULL DEFAULT '[]',
                erstellt_am TEXT NOT NULL
            );
            """
        )

        # Additive Migration für Bibliotheken, die vor der Mehrformat-
        # Unterstützung angelegt wurden: bestehende Dokumente gelten als
        # PDF ("seite"), bis sie durch die tatsächlich gespeicherten
        # Werte ersetzt werden (was für Alt-Zeilen nicht nötig ist, da
        # sie tatsächlich PDFs mit Seiten sind).
        _spalten_ergaenzen(
            conn,
            "dokumente",
            [
                ("dateityp", "TEXT NOT NULL DEFAULT 'pdf'"),
                ("einheit_typ", "TEXT NOT NULL DEFAULT 'seite'"),
                ("groesse_bytes", "INTEGER"),
            ],
        )
        _spalten_ergaenzen(
            conn,
            "chunks",
            [
                ("einheit_typ", "TEXT NOT NULL DEFAULT 'seite'"),
                ("einheit_anzeige", "TEXT"),
            ],
        )


def _jetzt():
    return datetime.now().isoformat(timespec="seconds")


# --- Dokumentbibliothek ---


def hash_berechnen(pdf_bytes):
    return hashlib.sha256(pdf_bytes).hexdigest()


def dokument_nach_hash(hash_wert):
    """Gibt das gespeicherte Dokument mit diesem Datei-Hash zurück (oder None)."""
    with _verbindung() as conn:
        zeile = conn.execute(
            "SELECT * FROM dokumente WHERE hash = ?", (hash_wert,)
        ).fetchone()
        return dict(zeile) if zeile else None


def dokument_speichern(dateiname, hash_wert, datei_bytes, einheiten_anzahl, dateityp, einheit_typ):
    """Speichert eine Dokumentdatei + Metadaten und gibt die neue Dokument-ID zurück.

    `einheiten_anzahl`/`einheit_typ` sind formatunabhängig zu verstehen:
    Seiten bei PDF, Folien bei PPTX, Tabellenblätter bei XLSX,
    Abschnitte bei DOCX/TXT/MD/CSV (siehe `dokument_verarbeitung.py`).
    Die Dateigröße wird direkt aus `datei_bytes` ermittelt.
    """
    with _verbindung() as conn:
        cursor = conn.execute(
            "INSERT INTO dokumente "
            "(dateiname, hash, seitenzahl, hochgeladen_am, dateityp, einheit_typ, groesse_bytes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                dateiname,
                hash_wert,
                einheiten_anzahl,
                _jetzt(),
                dateityp,
                einheit_typ,
                len(datei_bytes),
            ),
        )
        dokument_id = cursor.lastrowid

    (PDF_ORDNER / f"{hash_wert}.{dateityp}").write_bytes(datei_bytes)

    return dokument_id


def chunks_speichern(dokument_id, chunks, embeddings):
    """Speichert Chunks inkl. Embeddings zu einem Dokument."""
    with _verbindung() as conn:
        conn.executemany(
            "INSERT INTO chunks "
            "(dokument_id, seitennummer, text, embedding, einheit_typ, einheit_anzeige) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    dokument_id,
                    chunk["seitennummer"],
                    chunk["text"],
                    np.asarray(embedding, dtype=np.float32).tobytes(),
                    chunk.get("einheit_typ", "seite"),
                    chunk.get("einheit_anzeige"),
                )
                for chunk, embedding in zip(chunks, embeddings)
            ],
        )


def dokumente_laden():
    """Gibt alle Dokumente der Bibliothek zurück, neueste zuerst."""
    with _verbindung() as conn:
        zeilen = conn.execute(
            "SELECT * FROM dokumente ORDER BY hochgeladen_am DESC"
        ).fetchall()
        return [dict(zeile) for zeile in zeilen]


def dokument_loeschen(dokument_id):
    """Entfernt ein Dokument samt Chunks (Kaskade) und seiner PDF-Kopie.

    Bereinigt außerdem aktiv die Referenz auf diese Dokument-ID in
    `chats.dokument_ids` für alle Chats (nicht nur lazy beim nächsten
    Laden, siehe zusätzlich die Filterung in `chat_laden` als
    Absicherung für evtl. ältere Datenstände).
    """
    with _verbindung() as conn:
        zeile = conn.execute(
            "SELECT hash, dateityp FROM dokumente WHERE id = ?", (dokument_id,)
        ).fetchone()
        conn.execute("DELETE FROM dokumente WHERE id = ?", (dokument_id,))

        for chat_zeile in conn.execute("SELECT id, dokument_ids FROM chats").fetchall():
            vorhandene_ids = json.loads(chat_zeile["dokument_ids"])

            if dokument_id in vorhandene_ids:
                bereinigte_ids = [i for i in vorhandene_ids if i != dokument_id]
                conn.execute(
                    "UPDATE chats SET dokument_ids = ? WHERE id = ?",
                    (json.dumps(bereinigte_ids), chat_zeile["id"]),
                )

    if zeile:
        dateityp = zeile["dateityp"] or "pdf"
        (PDF_ORDNER / f"{zeile['hash']}.{dateityp}").unlink(missing_ok=True)


def chunks_laden(dokument_ids):
    """Lädt alle Chunks (inkl. Embedding als numpy-Array) der übergebenen Dokumente."""
    if not dokument_ids:
        return []

    platzhalter = ", ".join("?" for _ in dokument_ids)

    with _verbindung() as conn:
        zeilen = conn.execute(
            f"SELECT c.text, c.seitennummer, c.embedding, c.einheit_typ, "
            f"c.einheit_anzeige, d.dateiname "
            f"FROM chunks c JOIN dokumente d ON d.id = c.dokument_id "
            f"WHERE c.dokument_id IN ({platzhalter})",
            dokument_ids,
        ).fetchall()

    return [
        {
            "dateiname": zeile["dateiname"],
            "seitennummer": zeile["seitennummer"],
            "text": zeile["text"],
            "einheit_typ": zeile["einheit_typ"],
            "einheit_anzeige": zeile["einheit_anzeige"],
            "embedding": np.frombuffer(zeile["embedding"], dtype=np.float32),
        }
        for zeile in zeilen
    ]


# --- Chats ---


def chat_erstellen(titel=STANDARD_CHAT_TITEL):
    with _verbindung() as conn:
        jetzt = _jetzt()
        cursor = conn.execute(
            "INSERT INTO chats (titel, erstellt_am, aktualisiert_am, dokument_ids) "
            "VALUES (?, ?, ?, '[]')",
            (titel, jetzt, jetzt),
        )
        return cursor.lastrowid


def chat_liste():
    """Gibt alle Chats zurück (ohne Nachrichten), zuletzt aktualisiert zuerst."""
    with _verbindung() as conn:
        zeilen = conn.execute(
            "SELECT id, titel, erstellt_am, aktualisiert_am FROM chats "
            "ORDER BY aktualisiert_am DESC"
        ).fetchall()
        return [dict(zeile) for zeile in zeilen]


def _existierende_dokument_ids(conn, dokument_ids):
    if not dokument_ids:
        return []

    platzhalter = ", ".join("?" for _ in dokument_ids)
    zeilen = conn.execute(
        f"SELECT id FROM dokumente WHERE id IN ({platzhalter})", dokument_ids
    ).fetchall()
    vorhandene_ids = {zeile["id"] for zeile in zeilen}

    return [i for i in dokument_ids if i in vorhandene_ids]


def chat_laden(chat_id):
    """Lädt einen Chat inkl. Nachrichten, oder None falls nicht vorhanden.

    `dokument_ids` wird auf noch existierende Dokumente gefiltert, damit
    zwischenzeitlich gelöschte Dokumente nicht mehr als aktiv gelten.
    """
    with _verbindung() as conn:
        chat_zeile = conn.execute(
            "SELECT * FROM chats WHERE id = ?", (chat_id,)
        ).fetchone()

        if not chat_zeile:
            return None

        nachrichten_zeilen = conn.execute(
            "SELECT * FROM nachrichten WHERE chat_id = ? ORDER BY id",
            (chat_id,),
        ).fetchall()

        dokument_ids = _existierende_dokument_ids(
            conn, json.loads(chat_zeile["dokument_ids"])
        )

    chat = dict(chat_zeile)
    chat["dokument_ids"] = dokument_ids
    # Quellen bleiben in dem Format, in dem sie gespeichert wurden: neue
    # Nachrichten als Liste von Quellen-Dicts (siehe `quellen.py`), alte
    # (vor Mehrformat-Unterstützung) als Liste von [dateiname,
    # seitennummer]-Paaren. `quellen.formatiere_quellenhinweis`
    # normalisiert beide Formen - hier ist keine Migration nötig.
    chat["nachrichten"] = [
        {
            "frage": zeile["frage"],
            "antwort": zeile["antwort"],
            "quellen": json.loads(zeile["quellen"]),
        }
        for zeile in nachrichten_zeilen
    ]

    return chat


def chat_dokumente_setzen(chat_id, dokument_ids):
    with _verbindung() as conn:
        conn.execute(
            "UPDATE chats SET dokument_ids = ? WHERE id = ?",
            (json.dumps(list(dokument_ids)), chat_id),
        )


def chat_loeschen(chat_id):
    with _verbindung() as conn:
        conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))


def _kurztitel_erzeugen(frage):
    """Erzeugt einen kurzen deutschen Chat-Titel aus der ersten Frage.

    Bewusst eine einfache Heuristik statt eines eigenen API-Aufrufs, um
    keine zusätzlichen Kosten für die reine Titelgenerierung zu
    verursachen.
    """
    frage = frage.strip()

    if not frage:
        return STANDARD_CHAT_TITEL

    if len(frage) <= 45:
        return frage

    gekuerzt = frage[:45].rsplit(" ", 1)[0]

    return f"{gekuerzt}…" if gekuerzt else f"{frage[:45]}…"


def nachricht_hinzufuegen(chat_id, frage, antwort, quellen):
    """Speichert eine Chatrunde und aktualisiert Zeitstempel/Titel des Chats.

    Der Titel wird nur bei der ersten Nachricht eines Chats automatisch
    aus der Frage abgeleitet (und nur, wenn er noch der Standardtitel ist).
    """
    jetzt = _jetzt()

    with _verbindung() as conn:
        conn.execute(
            "INSERT INTO nachrichten (chat_id, frage, antwort, quellen, erstellt_am) "
            "VALUES (?, ?, ?, ?, ?)",
            (chat_id, frage, antwort, json.dumps(quellen), jetzt),
        )

        anzahl_zeile = conn.execute(
            "SELECT COUNT(*) AS anzahl FROM nachrichten WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()

        titel_zeile = conn.execute(
            "SELECT titel FROM chats WHERE id = ?", (chat_id,)
        ).fetchone()

        neuer_titel = titel_zeile["titel"]

        if anzahl_zeile["anzahl"] == 1 and neuer_titel == STANDARD_CHAT_TITEL:
            neuer_titel = _kurztitel_erzeugen(frage)

        conn.execute(
            "UPDATE chats SET aktualisiert_am = ?, titel = ? WHERE id = ?",
            (jetzt, neuer_titel, chat_id),
        )
