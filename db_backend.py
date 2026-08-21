"""Datenbank-Verbindungs-Abstraktion - vorbereitet für einen späteren

Wechsel von SQLite (Entwicklung) auf PostgreSQL (Produktion, z. B. IONOS
Managed PostgreSQL), siehe CLAUDE.md "Clevoriq Library Service".

WICHTIG - Umfang dieser Abstraktion (bewusst begrenzt, siehe CLAUDE.md):
Dieses Modul abstrahiert ausschließlich den VERBINDUNGSAUFBAU (welches
Backend, welche Zugangsdaten, welche Zeilen-/Cursor-Form). Es übersetzt
NICHT die SQLite-spezifische SQL-Syntax, die `speicher.py` aktuell
überall verwendet (`?`-Platzhalter, `INSERT OR IGNORE`, `PRAGMA
table_info`/`foreign_key_list`/`foreign_keys`, `cursor.lastrowid`,
`ALTER TABLE ... RENAME TO`/`legacy_alter_table` bei den
Alt-Datenbank-Migrationen). Diese Übersetzung ist eine große,
eigenständige Aufgabe (jede der ca. 150 Abfragen in `speicher.py`
müsste geprüft/portiert werden) und bewusst NICHT Teil dieses Blocks
("keine große unnötige Rewrite-Aktion") - deshalb weigert sich
`speicher.datenbank_initialisieren()` mit einer klaren Fehlermeldung,
wenn `CLEVORIQ_DATABASE_BACKEND=postgresql` gesetzt ist (siehe dort).
Die eigentliche Portierung der Abfragen ist für den nächsten
Architekturblock vorgesehen.

Der PostgreSQL-Verbindungsaufbau selbst (`postgresql_verbindung`) ist
trotzdem bereits ECHT implementiert und unabhängig testbar (siehe
`test_storage_db_backend.py`, das `psycopg2` durch ein Fake-Modul
ersetzt - keine echte Datenbankverbindung nötig) - er ist die
Grundlage, auf der der nächste Block die eigentliche Query-Portierung
aufbauen kann, ohne den Verbindungsaufbau neu erfinden zu müssen.
"""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


BACKEND_SQLITE = "sqlite"
BACKEND_POSTGRESQL = "postgresql"

_ENV_BACKEND = "CLEVORIQ_DATABASE_BACKEND"
_ENV_DATABASE_URL = "CLEVORIQ_DATABASE_URL"


class DatenbankFehler(Exception):
    """Einheitliche Ausnahme für Verbindungs-/Konfigurationsfehler,
    unabhängig vom tatsächlichen Backend - Aufrufer müssen nicht
    zwischen `sqlite3.Error` und einer künftigen `psycopg2`-Ausnahme
    unterscheiden. Enthält NIE die Datenbank-URL/Zugangsdaten im
    Nachrichtentext (siehe CLAUDE.md "Logging")."""


def aktuelles_backend():
    """Liest `CLEVORIQ_DATABASE_BACKEND` (Standard: `sqlite`)."""
    wert = os.environ.get(_ENV_BACKEND, BACKEND_SQLITE).strip().lower()
    return wert or BACKEND_SQLITE


@contextmanager
def sqlite_verbindung(db_pfad):
    """Der bisherige, unveränderte SQLite-Verbindungsaufbau (aus
    `speicher._verbindung` hierher verschoben, damit es nur EINE
    Implementierung gibt, die auch `speicher.py` selbst nutzt)."""
    db_pfad = Path(db_pfad)
    db_pfad.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_pfad)
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


def _lade_psycopg2():
    """Eigene, kleine Ladefunktion statt eines Modul-Top-Level-Imports -
    zwei Gründe: (1) ein reiner SQLite-Betrieb (der Normalfall in
    diesem Block) soll `psycopg2` nicht installiert haben müssen, (2)
    Tests können diese Funktion gezielt durch ein Fake-Modul ersetzen
    (`unittest.mock.patch`), ohne dass `psycopg2` real installiert sein
    muss - siehe `test_storage_db_backend.py`."""
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError as fehler:
        raise DatenbankFehler(
            "CLEVORIQ_DATABASE_BACKEND=postgresql ist gesetzt, aber das "
            "Paket 'psycopg2-binary' ist nicht installiert (siehe "
            "requirements.txt)."
        ) from fehler

    return psycopg2


@contextmanager
def postgresql_verbindung(database_url):
    """Baut eine echte PostgreSQL-Verbindung auf (`psycopg2`), mit
    Dict-artigem Zeilenzugriff (`RealDictCursor`), damit `zeile["spalte"]`
    - wie bei `sqlite3.Row` - funktioniert, sobald `speicher.py`s
    Abfragen im nächsten Block portiert werden. Derselbe
    Commit-bei-Erfolg/Rollback-bei-Fehler-Vertrag wie `sqlite_verbindung`.

    Wirft `DatenbankFehler` (nie die rohe `psycopg2`-Ausnahme, die eine
    Verbindungs-URL mit Zugangsdaten enthalten könnte) bei jedem
    Verbindungsfehler.
    """
    if not database_url:
        raise DatenbankFehler(
            f"CLEVORIQ_DATABASE_BACKEND=postgresql ist gesetzt, aber "
            f"{_ENV_DATABASE_URL} fehlt."
        )

    psycopg2 = _lade_psycopg2()

    try:
        conn = psycopg2.connect(database_url, cursor_factory=psycopg2.extras.RealDictCursor)
    except Exception as fehler:
        # Nachricht nennt bewusst NICHT `database_url` (könnte
        # Zugangsdaten enthalten) - siehe CLAUDE.md "Logging".
        raise DatenbankFehler("Verbindung zur PostgreSQL-Datenbank fehlgeschlagen.") from fehler

    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def verbindung(sqlite_pfad):
    """Zentrale Verbindungs-Fabrik: liefert je nach konfiguriertem
    Backend einen SQLite- oder PostgreSQL-Verbindungs-Kontextmanager.

    `sqlite_pfad` wird nur für Backend `sqlite` verwendet - vom Aufrufer
    (`speicher.py`) übergeben, damit dessen (in Tests umgeleiteter)
    `DB_PFAD` maßgeblich bleibt, statt hier einen eigenen, festen Pfad
    zu verwenden.
    """
    backend = aktuelles_backend()

    if backend == BACKEND_POSTGRESQL:
        return postgresql_verbindung(os.environ.get(_ENV_DATABASE_URL, "").strip())

    if backend != BACKEND_SQLITE:
        raise DatenbankFehler(
            f"Unbekanntes {_ENV_BACKEND}={backend!r} - erlaubt sind "
            f"{BACKEND_SQLITE!r} oder {BACKEND_POSTGRESQL!r}."
        )

    return sqlite_verbindung(sqlite_pfad)
