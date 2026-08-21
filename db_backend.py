"""Datenbank-Abstraktion - SQLite (Entwicklung) UND PostgreSQL (Produktion,

z. B. IONOS Managed PostgreSQL) mit EINER Business-Logik in `speicher.py`,
siehe CLAUDE.md "Dual-Backend-Architektur".

Zwei Ebenen:

1. **Verbindungsaufbau** (`verbindung`/`sqlite_verbindung`/`postgresql_verbindung`):
   welches Backend, welche Zugangsdaten, Commit-bei-Erfolg/Rollback-bei-
   Fehler.
2. **Portable Verbindungs-/Cursor-Hülle** (`_PortableConnection`/
   `_PortableCursor`): macht die beiden Treiber (`sqlite3`/`psycopg2`) für
   `speicher.py` ununterscheidbar, OHNE dass jede einzelne der ca. 150
   Abfragen dort umgeschrieben werden musste:
   - `?`-Platzhalter werden automatisch zu `%s` übersetzt, wenn das
     Backend PostgreSQL ist (reine Text-Übersetzung der SQL-Zeichenkette
     VOR dem Ausführen - siehe `_platzhalter_uebersetzen` - niemals der
     Parameter-*Werte* selbst, die bleiben immer gebunden/parametrisiert,
     nie String-interpoliert; sicher geprüft, dass in `speicher.py`
     nirgends ein literales "?" innerhalb eines SQL-String-Literals steht).
   - `fetchone()`/`fetchall()` geben IMMER ein reines `dict`/eine Liste
     reiner `dict`s zurück (`sqlite3.Row` UND `psycopg2.extras.RealDictRow`
     sind beide bereits Mapping-artig - `dict(...)` normalisiert beide auf
     dieselbe, einfache Schnittstelle).
   - `rowcount` wird durchgereicht (auf beiden Treibern identisch
     verfügbar).
   - `lastrowid` funktioniert nur unter SQLite (`psycopg2` kennt das
     Konzept nicht) - Aufrufer, die eine neu erzeugte ID brauchen, nutzen
     stattdessen `insert_und_id_zurueckgeben` (siehe unten), das für
     BEIDE Backends korrekt funktioniert. Ein direkter Zugriff auf
     `.lastrowid` unter PostgreSQL wirft bewusst laut `DatenbankFehler`
     statt still `None`/`AttributeError` zu liefern.

Was WEITERHIN nicht hier, sondern nur für SQLite existiert (siehe
`speicher.py`s `_dokumente_tabelle_neu_aufbauen`/`_chunks_tabelle_neu_aufbauen`/
`_chats_tabelle_neu_aufbauen` und ihre Prüf-Funktionen): die drei
einmaligen Reparatur-Migrationen für SEHR alte, noch vor der
Mehrbenutzer-Umstellung angelegte SQLite-Datenbanken (`PRAGMA
legacy_alter_table`, `ALTER TABLE ... RENAME TO`, rohe
`sqlite3.connect`). Eine frische PostgreSQL-Datenbank hat diesen
historischen Schaden nie, braucht diese Reparaturen also grundsätzlich
nicht - `datenbank_initialisieren` überspringt sie für PostgreSQL
vollständig, statt sie zu portieren.
"""

import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path


BACKEND_SQLITE = "sqlite"
BACKEND_POSTGRESQL = "postgresql"

_ENV_BACKEND = "CLEVORIQ_DATABASE_BACKEND"
_ENV_DATABASE_URL = "CLEVORIQ_DATABASE_URL"

# Erlaubte, in `.env.example` dokumentierte SSL-Modi für die spätere
# verschlüsselte PostgreSQL-Verbindung (siehe `postgresql_verbindung`) -
# identisch zu psycopg2s/libpq's eigenen `sslmode`-Werten, hier nur
# zwecks klarer Fehlermeldung bei einem Tippfehler validiert.
_GUELTIGE_SSL_MODI = {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}
_ENV_SSL_MODE = "CLEVORIQ_DATABASE_SSLMODE"


class DatenbankFehler(Exception):
    """Einheitliche Ausnahme für Verbindungs-/Konfigurations-/Zugriffsfehler,
    unabhängig vom tatsächlichen Backend - Aufrufer müssen nicht
    zwischen `sqlite3.Error` und `psycopg2`-Ausnahmen unterscheiden.
    Enthält NIE die Datenbank-URL/Zugangsdaten im Nachrichtentext (siehe
    CLAUDE.md "Logging")."""


def aktuelles_backend():
    """Liest `CLEVORIQ_DATABASE_BACKEND` (Standard: `sqlite`)."""
    wert = os.environ.get(_ENV_BACKEND, BACKEND_SQLITE).strip().lower()
    return wert or BACKEND_SQLITE


def ist_postgresql():
    return aktuelles_backend() == BACKEND_POSTGRESQL


# --- Platzhalter-/Zeilen-/ID-Portabilität ---
#
# `speicher.py` schreibt JEDE Abfrage weiterhin mit SQLite-Syntax (`?`
# als Platzhalter) - `_PortableConnection` übersetzt das transparent für
# PostgreSQL. Sicherheitsrelevant: das ist eine reine SQL-TEXT-
# Übersetzung (ein Zeichen gegen eine Zeichenkette), NIEMALS eine
# Einbettung von Parameter-*Werten* in den SQL-Text - die Werte bleiben
# in jedem Fall gebundene Parameter, die der jeweilige Treiber selbst
# sicher escaped. Verifiziert (siehe CLAUDE.md), dass in `speicher.py`
# nirgends ein literales "?"-Zeichen innerhalb eines SQL-String-Literals
# vorkommt, das fälschlich mitübersetzt würde.
_PLATZHALTER_MUSTER = re.compile(r"\?")


def _platzhalter_uebersetzen(sql):
    return _PLATZHALTER_MUSTER.sub("%s", sql)


class _PortableCursor:
    """Hülle um einen `sqlite3`- oder `psycopg2`-Cursor - normalisiert
    Zeilen auf reine `dict`s, reicht `rowcount` durch, macht `lastrowid`
    unter PostgreSQL zu einem lauten Fehler statt eines stillen `None`."""

    def __init__(self, roher_cursor, dialekt):
        self._cursor = roher_cursor
        self._dialekt = dialekt

    def fetchone(self):
        zeile = self._cursor.fetchone()
        return dict(zeile) if zeile is not None else None

    def fetchall(self):
        return [dict(zeile) for zeile in self._cursor.fetchall()]

    def __iter__(self):
        for zeile in self._cursor:
            yield dict(zeile)

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def lastrowid(self):
        if self._dialekt == BACKEND_POSTGRESQL:
            raise DatenbankFehler(
                "cursor.lastrowid ist unter PostgreSQL nicht verfügbar - "
                "nutze db_backend.insert_und_id_zurueckgeben(...)."
            )
        return self._cursor.lastrowid


class _PortableConnection:
    """Hülle um eine `sqlite3`- oder `psycopg2`-Verbindung mit EINER
    Schnittstelle für `speicher.py` - siehe Moduldocstring."""

    def __init__(self, rohe_verbindung, dialekt):
        self._conn = rohe_verbindung
        self._dialekt = dialekt

    def execute(self, sql, params=()):
        if self._dialekt == BACKEND_POSTGRESQL:
            sql = _platzhalter_uebersetzen(sql)

        cursor = self._conn.cursor()
        cursor.execute(sql, params)
        return _PortableCursor(cursor, self._dialekt)

    def executemany(self, sql, seq_of_params):
        if self._dialekt == BACKEND_POSTGRESQL:
            sql = _platzhalter_uebersetzen(sql)

        cursor = self._conn.cursor()
        cursor.executemany(sql, list(seq_of_params))
        return _PortableCursor(cursor, self._dialekt)

    def executescript(self, sql):
        """Führt ein Mehr-Anweisungen-SQL-Skript aus (Schema-Erstellung,
        siehe `speicher.datenbank_initialisieren`). SQLite hat dafür eine
        eigene Methode; `psycopg2` führt mehrere `;`-getrennte
        Anweisungen bereits über ein einzelnes `cursor.execute(...)`
        aus, daher genügt dort ein normaler Aufruf."""
        if self._dialekt == BACKEND_POSTGRESQL:
            self._conn.cursor().execute(sql)
        else:
            self._conn.executescript(sql)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


@contextmanager
def sqlite_verbindung(db_pfad):
    """SQLite-Verbindungsaufbau, portabel gehüllt (siehe `_PortableConnection`)."""
    db_pfad = Path(db_pfad)
    db_pfad.parent.mkdir(parents=True, exist_ok=True)

    roh = sqlite3.connect(db_pfad)
    roh.row_factory = sqlite3.Row
    roh.execute("PRAGMA foreign_keys = ON")
    conn = _PortableConnection(roh, BACKEND_SQLITE)

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
    zwei Gründe: (1) ein reiner SQLite-Betrieb soll `psycopg2` nicht
    installiert haben müssen, (2) Tests können diese Funktion gezielt
    durch ein Fake-Modul ersetzen (`unittest.mock.patch`), ohne dass
    `psycopg2` real installiert sein muss - siehe
    `test_storage_db_backend.py`."""
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


def _ssl_modus():
    """Liest `CLEVORIQ_DATABASE_SSLMODE` (Standard: `prefer`, dieselbe
    Voreinstellung wie libpq selbst) - für die spätere, echte IONOS-
    Verbindung sollte dies auf `require`/`verify-full` gesetzt werden
    (siehe `.env.example`/CLAUDE.md "verschlüsselte DB-Kommunikation").
    Ein ungültiger Wert scheitert klar statt still einen unsicheren
    Modus zu verwenden."""
    wert = os.environ.get(_ENV_SSL_MODE, "prefer").strip().lower() or "prefer"

    if wert not in _GUELTIGE_SSL_MODI:
        raise DatenbankFehler(
            f"{_ENV_SSL_MODE}={wert!r} ist kein gültiger PostgreSQL-SSL-Modus "
            f"(erlaubt: {', '.join(sorted(_GUELTIGE_SSL_MODI))})."
        )

    return wert


@contextmanager
def postgresql_verbindung(database_url):
    """Baut eine echte PostgreSQL-Verbindung auf (`psycopg2`), portabel
    gehüllt (siehe `_PortableConnection`) - `zeile["spalte"]` und
    `dict(zeile)` verhalten sich danach identisch zu SQLite. Verschlüsselt
    per `sslmode` (siehe `_ssl_modus`), sofern der Connection-String
    selbst keinen eigenen `sslmode`-Parameter setzt. Derselbe
    Commit-bei-Erfolg/Rollback-bei-Fehler-Vertrag wie `sqlite_verbindung`.

    Wirft `DatenbankFehler` (NIE die rohe `psycopg2`-Ausnahme, die die
    Verbindungs-URL mit Zugangsdaten enthalten könnte) bei jedem
    Verbindungsfehler - die URL selbst wird an KEINER Stelle geloggt,
    in eine Ausnahmenachricht eingebettet oder sonst ausgegeben.
    """
    if not database_url:
        raise DatenbankFehler(
            f"CLEVORIQ_DATABASE_BACKEND=postgresql ist gesetzt, aber "
            f"{_ENV_DATABASE_URL} fehlt."
        )

    psycopg2 = _lade_psycopg2()

    try:
        roh = psycopg2.connect(
            database_url, cursor_factory=psycopg2.extras.RealDictCursor, sslmode=_ssl_modus()
        )
    except DatenbankFehler:
        raise
    except Exception:
        # Nachricht nennt bewusst NICHT `database_url` (könnte
        # Zugangsdaten enthalten) - siehe CLAUDE.md "Logging". `from None`
        # kappt bewusst die Ausnahme-Kette: die rohe `psycopg2`-Ausnahme
        # kann (je nach Treiber-/libpq-Version) Teile des Connection-
        # Strings in ihrer eigenen Nachricht enthalten - ein vollständiger
        # Traceback (z. B. in einem Log) darf sie deshalb nicht mit
        # ausgeben, selbst nicht verkettet als `__cause__`.
        raise DatenbankFehler("Verbindung zur PostgreSQL-Datenbank fehlgeschlagen.") from None

    conn = _PortableConnection(roh, BACKEND_POSTGRESQL)

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
    Backend einen SQLite- oder PostgreSQL-Verbindungs-Kontextmanager,
    beide mit identischer, portabler Schnittstelle.

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


# --- Schema-/Migrations-Helfer (dialektübergreifend) ---


def spalten_vorhanden(conn, tabelle):
    """Gibt die Menge der tatsächlich vorhandenen Spaltennamen einer
    Tabelle zurück - dialektabhängig (`PRAGMA table_info` unter SQLite,
    `information_schema.columns` unter PostgreSQL), aber mit identischem
    Rückgabewert, sodass `speicher._spalten_ergaenzen` selbst nicht
    zwischen den Backends unterscheiden muss.

    `tabelle` MUSS ein vertrauenswürdiger, im Code fest verdrahteter
    Bezeichner sein (nie eine Benutzereingabe) - er wird für SQLite
    direkt in die `PRAGMA`-Anweisung eingesetzt (PRAGMA erlaubt dort
    keine gebundenen Parameter), für PostgreSQL dagegen ganz regulär als
    gebundener Parameter übergeben.
    """
    if ist_postgresql():
        zeilen = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            (tabelle,),
        ).fetchall()
        return {zeile["column_name"] for zeile in zeilen}

    zeilen = conn.execute(f"PRAGMA table_info({tabelle})").fetchall()
    return {zeile["name"] for zeile in zeilen}


def insert_und_id_zurueckgeben(conn, sql, params, id_spalte="id"):
    """Führt ein INSERT aus und gibt die neu erzeugte ID zurück - für
    BEIDE Backends korrekt (SQLite: `cursor.lastrowid`; PostgreSQL: das
    übergebene `sql` bekommt automatisch ` RETURNING {id_spalte}`
    angehängt und die ID wird aus der zurückgegebenen Zeile gelesen).
    Ersetzt jede direkte Nutzung von `cursor.lastrowid` in `speicher.py`
    - siehe CLAUDE.md "Insert + ID-Rückgabe", kein `SELECT MAX(id)`-Workaround.

    `sql` darf selbst KEIN `RETURNING` enthalten (wird hier ergänzt).
    """
    if ist_postgresql():
        zeile = conn.execute(f"{sql} RETURNING {id_spalte}", params).fetchone()
        return zeile[id_spalte]

    cursor = conn.execute(sql, params)
    return cursor.lastrowid


def upsert_ignore(conn, tabelle, spalten, werte, konflikt_spalten):
    """Fügt eine Zeile ein, NUR wenn noch keine mit denselben
    `konflikt_spalten`-Werten existiert - sonst ein No-Op (dieselbe
    fachliche Semantik wie SQLites `INSERT OR IGNORE`, portiert auf
    PostgreSQLs `ON CONFLICT (...) DO NOTHING`; NIEMALS ein blindes
    Text-Ersetzen, siehe CLAUDE.md "Upsert/Conflict-Semantik" - eine
    bereits bestehende Zeile wird in KEINEM der beiden Fälle
    überschrieben, auch nicht teilweise).

    `tabelle`/`spalten`/`konflikt_spalten` sind IMMER vertrauenswürdige,
    im Code fest verdrahtete Bezeichner (nie Benutzereingaben) - sie
    werden als reiner SQL-Text eingesetzt, `werte` bleiben gebundene
    Parameter.
    """
    spalten_liste = ", ".join(spalten)
    platzhalter = ", ".join("?" for _ in spalten)

    if ist_postgresql():
        konflikt = ", ".join(konflikt_spalten)
        sql = (
            f"INSERT INTO {tabelle} ({spalten_liste}) VALUES ({platzhalter}) "
            f"ON CONFLICT ({konflikt}) DO NOTHING"
        )
    else:
        sql = f"INSERT OR IGNORE INTO {tabelle} ({spalten_liste}) VALUES ({platzhalter})"

    conn.execute(sql, werte)


def upsert_ersetzen(conn, tabelle, konflikt_spalten, spalten_werte):
    """Fügt eine Zeile ein ODER aktualisiert die bestehende (bei
    Konflikt auf `konflikt_spalten`) atomar in EINER Anweisung - ersetzt
    ein race-anfälliges "erst SELECT, dann INSERT-oder-UPDATE"-Muster in
    Python (siehe CLAUDE.md "Concurrency"). SQLite: `INSERT ... ON
    CONFLICT (...) DO UPDATE SET ...` (seit 3.24, von SQLites eigener
    `INSERT OR REPLACE` bewusst NICHT verwendet, weil das bei
    Fremdschlüssel-Referenzen eine physische DELETE+INSERT-Operation
    macht statt eines echten UPDATE - u. a. würde das die `user_id`-PK-
    Semantik von `zwei_faktor` unnötig verkomplizieren). PostgreSQL:
    identische `ON CONFLICT ... DO UPDATE SET ...`-Syntax.

    `spalten_werte` ist ein `dict {spaltenname: wert}` - alle Spalten
    außer denen in `konflikt_spalten` werden bei einem Konflikt auf den
    neuen Wert aktualisiert.
    """
    alle_spalten = list(spalten_werte.keys())
    platzhalter = ", ".join("?" for _ in alle_spalten)
    spalten_liste = ", ".join(alle_spalten)
    konflikt = ", ".join(konflikt_spalten)
    update_spalten = [s for s in alle_spalten if s not in konflikt_spalten]
    update_klausel = ", ".join(f"{s} = excluded.{s}" for s in update_spalten)

    sql = (
        f"INSERT INTO {tabelle} ({spalten_liste}) VALUES ({platzhalter}) "
        f"ON CONFLICT ({konflikt}) DO UPDATE SET {update_klausel}"
    )
    conn.execute(sql, list(spalten_werte.values()))
