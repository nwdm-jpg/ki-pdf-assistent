"""Persistenz für Benutzerkonten, Dokumentbibliothek und Chats (SQLite +
lokale Dateikopien), mit strikter Trennung der Daten je Benutzer.

Alle Daten liegen im projektlokalen Ordner `app_daten/` (nicht Teil des
Git-Repos): `app_daten/bibliothek.db` für Benutzerkonten, Metadaten,
Chunks, Embeddings, Chats und Nachrichten; `app_daten/users/<user_id>/documents/`
für die Originaldateien (benannt nach ihrem Datei-Hash + tatsächlicher
Endung, zur Duplikaterkennung je Benutzer) - jeder Benutzer bekommt einen
eigenen Unterordner, damit ein Benutzer nicht über einen erratenen Pfad
auf die Datei eines anderen zugreifen kann. Der Ordnername war vor der
Mehrbenutzer-Umstellung `app_daten/pdfs/` (siehe Migration unten).

WICHTIG - Prinzip der strikten Datentrennung: Jede Funktion, die
Dokumente, Chunks, Chats oder Nachrichten liest, schreibt oder löscht,
verlangt einen `benutzer_id`-Parameter und filtert JEDE SQL-Abfrage
danach (`WHERE user_id = ?` bzw. über einen Join auf `dokumente`/`chats`).
Es gibt bewusst keine Funktion, die Dokumente oder Chats ohne
Benutzerbezug liest - die Trennung ist damit in der Persistenzschicht
selbst erzwungen, nicht nur durch Filterung in der UI (`web_app.py`
reicht `benutzer_id` aus der angemeldeten Sitzung durch, verlässt sich
aber nicht darauf als einzige Schutzschicht).
"""

import hashlib
import json
import secrets
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

import auth


# Gültigkeitsdauer sicherheitsrelevanter Einmal-Tokens (siehe
# `email_verifizierung_erstellen`/`passwort_reset_anfordern`) - bewusst
# als Konstanten statt Magic Numbers, damit sie an einer Stelle
# nachvollziehbar und leicht anpassbar sind.
EMAIL_VERIFIZIERUNG_GUELTIGKEIT_STUNDEN = 24
PASSWORT_RESET_GUELTIGKEIT_STUNDEN = 1


APP_DATEN_ORDNER = Path(__file__).resolve().parent / "app_daten"
BENUTZER_ORDNER = APP_DATEN_ORDNER / "users"
DB_PFAD = APP_DATEN_ORDNER / "bibliothek.db"

# Historischer Name (vor Mehrbenutzer-Umstellung war dies der einzige,
# gemeinsame Ablageordner aller Originaldateien) - bleibt für die
# Migration bestehender Dateien in die neue Pro-Benutzer-Struktur
# relevant, siehe `_dateien_migrieren`.
_ALTER_PDF_ORDNER = APP_DATEN_ORDNER / "pdfs"

STANDARD_CHAT_TITEL = "Neuer Chat"

# Konto, dem beim Upgrade einer bestehenden (vor Authentifizierung
# angelegten) Datenbank alle bis dahin eigentümerlosen Dokumente/Chats
# zugewiesen werden, damit keine bestehenden Testdaten verloren gehen
# (siehe `_migration_bestandsdaten_zuweisen`). Zugangsdaten sind bewusst
# fest und einfach, da dies ein lokales Entwicklungs-Bootstrap-Konto ist
# - siehe Hinweis im Abschluss-Bericht des Auth-Features.
MIGRATIONS_BENUTZERNAME = "altbestand"
MIGRATIONS_EMAIL = "altbestand@avenloq.local"
MIGRATIONS_PASSWORT = "Altbestand123!"


@contextmanager
def _verbindung():
    APP_DATEN_ORDNER.mkdir(exist_ok=True)

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


def _dokumente_tabelle_pro_benutzer_eindeutig(conn):
    """Prüft, ob `dokumente` bereits die Zusatz-Eindeutigkeit (hash, user_id) hat.

    Vor der Mehrbenutzer-Umstellung war `hash` allein global eindeutig
    (ein Duplikat-Schutz über ALLE Benutzer hinweg) - das würde einem
    zweiten Benutzer das Hochladen einer inhaltsgleichen Datei
    verwehren bzw. schlimmer: der Duplikat-Check fände die Zeile des
    ersten Benutzers und der Upload des zweiten würde still nichts
    speichern. Erkannt wird der aktuelle Stand am Tabellen-DDL in
    `sqlite_master`, das bei einer bereits migrierten Tabelle das
    zusammengesetzte UNIQUE(hash, user_id) enthält.
    """
    zeile = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='dokumente'"
    ).fetchone()
    return bool(zeile) and "UNIQUE(hash, user_id)" in (zeile["sql"] or "")


def _dokumente_tabelle_neu_aufbauen():
    """Baut `dokumente` mit zusammengesetzter UNIQUE(hash, user_id) neu auf.

    Läuft bewusst über eine EIGENE, rohe sqlite3-Verbindung ohne
    `PRAGMA foreign_keys = ON` (anders als `_verbindung()`): SQLite
    lässt `PRAGMA foreign_keys` nicht innerhalb einer bereits laufenden
    Transaktion umschalten, und der Standard-Wert einer frischen
    Verbindung ist ohnehin AUS - der Rename/Neuaufbau/Kopiervorgang
    unten würde mit aktivierten Fremdschlüsseln sonst an der von
    `chunks.dokument_id` referenzierten Tabelle scheitern. Alle
    bestehenden IDs bleiben exakt erhalten (expliziter INSERT der
    Original-ID-Werte), damit `chunks.dokument_id` und die
    `dokument_ids`-Listen in `chats` weiter gültig bleiben. Wird nur
    aufgerufen, wenn `_dokumente_tabelle_pro_benutzer_eindeutig` False
    liefert (einmalig pro Datenbank).

    `PRAGMA legacy_alter_table = ON` ist hier zwingend nötig: SQLite
    schreibt bei `ALTER TABLE ... RENAME TO ...` seit 3.25 standardmäßig
    Fremdschlüssel-Definitionen ANDERER Tabellen, die auf die
    umbenannte Tabelle verweisen, automatisch auf den neuen Namen um -
    `chunks.dokument_id REFERENCES dokumente(id)` würde beim Umbenennen
    von `dokumente` also zu `REFERENCES dokumente_migration_alt(id)`
    umgeschrieben und würde nach dem `DROP TABLE dokumente_migration_alt`
    unten auf eine nicht mehr existierende Tabelle zeigen (siehe
    `_chunks_tabelle_neu_aufbauen`, das genau diesen bereits eingetretenen
    Schaden aus vor diesem Fix migrierten Datenbanken repariert). Mit
    `legacy_alter_table = ON` bleibt die Fremdschlüssel-Definition in
    `chunks` unverändert beim (wieder gültigen) Namen `dokumente`.
    """
    conn = sqlite3.connect(DB_PFAD)
    conn.row_factory = sqlite3.Row

    try:
        conn.execute("PRAGMA legacy_alter_table = ON")
        conn.execute("ALTER TABLE dokumente RENAME TO dokumente_migration_alt")
        conn.execute(
            """
            CREATE TABLE dokumente (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES benutzer(id) ON DELETE CASCADE,
                dateiname TEXT NOT NULL,
                hash TEXT NOT NULL,
                seitenzahl INTEGER NOT NULL,
                hochgeladen_am TEXT NOT NULL,
                dateityp TEXT NOT NULL DEFAULT 'pdf',
                einheit_typ TEXT NOT NULL DEFAULT 'seite',
                groesse_bytes INTEGER,
                UNIQUE(hash, user_id)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO dokumente
                (id, user_id, dateiname, hash, seitenzahl, hochgeladen_am,
                 dateityp, einheit_typ, groesse_bytes)
            SELECT id, user_id, dateiname, hash, seitenzahl, hochgeladen_am,
                   dateityp, einheit_typ, groesse_bytes
            FROM dokumente_migration_alt
            """
        )
        conn.execute("DROP TABLE dokumente_migration_alt")
        conn.execute("PRAGMA legacy_alter_table = OFF")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _chunks_tabelle_fremdschluessel_defekt(conn):
    """Erkennt, ob `chunks.dokument_id` auf die längst gelöschte
    Zwischentabelle `dokumente_migration_alt` statt auf `dokumente` zeigt.

    Betrifft jede Datenbank, die den `dokumente`-Neuaufbau oben VOR der
    `PRAGMA legacy_alter_table`-Absicherung durchlaufen hat: SQLite hatte
    die Fremdschlüssel-Definition in `chunks` beim Umbenennen von
    `dokumente` automatisch auf `dokumente_migration_alt` umgeschrieben;
    diese Tabelle existiert danach nicht mehr, weshalb jede
    fremdschlüsselgeprüfte Schreiboperation auf `chunks` (z. B. neue
    Chunks nach einem Upload, siehe `chunks_speichern`, immer unter
    `PRAGMA foreign_keys = ON` aus `_verbindung()`) seitdem mit "no such
    table: main.dokumente_migration_alt" fehlschlägt. Geprüft wird direkt
    am gespeicherten Tabellen-DDL in `sqlite_master`.
    """
    zeile = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='chunks'"
    ).fetchone()
    return bool(zeile) and "dokumente_migration_alt" in (zeile["sql"] or "")


def _chunks_tabelle_neu_aufbauen():
    """Repariert eine `chunks`-Fremdschlüssel-Referenz, die fälschlich auf
    `dokumente_migration_alt` statt auf `dokumente` zeigt (siehe
    `_chunks_tabelle_fremdschluessel_defekt`).

    Idempotent und sicher: prüft vorab, ob überhaupt ein Schaden
    vorliegt, und tut sonst nichts - kann bei jedem Start beliebig oft
    ausgeführt werden, auch gegen eine bereits reparierte oder eine nie
    betroffene (frische) Datenbank. Baut `chunks` - analog zu
    `_dokumente_tabelle_neu_aufbauen` - über eine eigene rohe Verbindung
    neu auf, mit `PRAGMA legacy_alter_table = ON` während der Umbenennung
    (nichts referenziert `chunks` selbst per Fremdschlüssel, aber
    dieselbe Absicherung schadet nicht). Alle bestehenden Chunk-IDs und
    -Daten inkl. Embeddings bleiben exakt erhalten (expliziter INSERT der
    Original-ID-Werte).
    """
    conn = sqlite3.connect(DB_PFAD)
    conn.row_factory = sqlite3.Row

    try:
        if not _chunks_tabelle_fremdschluessel_defekt(conn):
            return

        conn.execute("PRAGMA legacy_alter_table = ON")
        conn.execute("ALTER TABLE chunks RENAME TO chunks_migration_alt")
        conn.execute(
            """
            CREATE TABLE chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dokument_id INTEGER NOT NULL REFERENCES dokumente(id) ON DELETE CASCADE,
                seitennummer INTEGER NOT NULL,
                text TEXT NOT NULL,
                embedding BLOB NOT NULL,
                einheit_typ TEXT NOT NULL DEFAULT 'seite',
                einheit_anzeige TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO chunks
                (id, dokument_id, seitennummer, text, embedding, einheit_typ, einheit_anzeige)
            SELECT id, dokument_id, seitennummer, text, embedding, einheit_typ, einheit_anzeige
            FROM chunks_migration_alt
            """
        )
        conn.execute("DROP TABLE chunks_migration_alt")
        conn.execute("PRAGMA legacy_alter_table = OFF")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _chats_tabelle_user_fk_kaskadiert(conn):
    """Prüft, ob `chats.user_id` tatsächlich `ON DELETE CASCADE` trägt.

    Bei einer Datenbank, die vor der Mehrbenutzer-Umstellung angelegt
    wurde, kam die Spalte `user_id` nachträglich per additivem `ALTER
    TABLE ... ADD COLUMN` hinzu (siehe `_spalten_ergaenzen`-Aufruf in
    `datenbank_initialisieren`) - SQLites `ADD COLUMN` kann dabei aber
    keine `ON DELETE CASCADE`-Klausel setzen (nur eine einfache
    `REFERENCES benutzer(id)` ohne Kaskade), anders als bei einer frisch
    anlegten Tabelle (siehe `CREATE TABLE IF NOT EXISTS chats` oben, die
    die Klausel von Anfang an enthält). Ohne Kaskade würde das Löschen
    eines Benutzerkontos (`konto_endgueltig_loeschen`) an dessen
    verbliebenen Chats mit einem Fremdschlüssel-Fehler scheitern, statt
    sie automatisch mitzulöschen - genau die in Abschnitt 9 verlangte
    Prüfung "vor Aktivierung der Kontolöschung". Geprüft wird über
    `PRAGMA foreign_key_list`, nicht über einen reinen DDL-Textvergleich
    wie bei `_chunks_tabelle_fremdschluessel_defekt`, weil hier nicht
    nach einem falschen Tabellennamen gesucht wird, sondern nach dem
    tatsächlichen `on_delete`-Verhalten der bestehenden Fremdschlüssel-
    Definition.
    """
    for zeile in conn.execute("PRAGMA foreign_key_list(chats)"):
        if zeile["table"] == "benutzer" and zeile["from"] == "user_id":
            return (zeile["on_delete"] or "").upper() == "CASCADE"

    # Keine solche Fremdschlüssel-Definition gefunden (sollte nach
    # `datenbank_initialisieren`s additiver Spalten-Migration nicht
    # vorkommen) - als "nicht kaskadiert" behandeln, sicherheitshalber.
    return False


def _chats_tabelle_neu_aufbauen():
    """Baut `chats` mit korrekt kaskadierendem `user_id`-Fremdschlüssel neu auf.

    Idempotent (prüft vorab über `_chats_tabelle_user_fk_kaskadiert` und
    tut sonst nichts) und sicher gegenüber dem in `_dokumente_tabelle_neu_aufbauen`
    dokumentierten SQLite-Verhalten: `PRAGMA legacy_alter_table = ON`
    verhindert, dass das `RENAME TO` unten die Fremdschlüssel-Definition
    von `nachrichten.chat_id` (die auf `chats(id)` verweist) automatisch
    auf den Zwischennamen umschreibt. Alle bestehenden Chat-IDs, Titel,
    Zeitstempel und `dokument_ids`-Zuordnungen bleiben exakt erhalten
    (expliziter INSERT der Original-ID-Werte) - `nachrichten.chat_id`
    bleibt dadurch für jede bestehende Nachricht gültig.
    """
    conn = sqlite3.connect(DB_PFAD)
    conn.row_factory = sqlite3.Row

    try:
        if _chats_tabelle_user_fk_kaskadiert(conn):
            return

        conn.execute("PRAGMA legacy_alter_table = ON")
        conn.execute("ALTER TABLE chats RENAME TO chats_migration_alt")
        conn.execute(
            """
            CREATE TABLE chats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES benutzer(id) ON DELETE CASCADE,
                titel TEXT NOT NULL,
                erstellt_am TEXT NOT NULL,
                aktualisiert_am TEXT NOT NULL,
                dokument_ids TEXT NOT NULL DEFAULT '[]'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO chats
                (id, user_id, titel, erstellt_am, aktualisiert_am, dokument_ids)
            SELECT id, user_id, titel, erstellt_am, aktualisiert_am, dokument_ids
            FROM chats_migration_alt
            """
        )
        conn.execute("DROP TABLE chats_migration_alt")
        conn.execute("PRAGMA legacy_alter_table = OFF")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _migrations_benutzer_id(conn):
    """Stellt sicher, dass das Bootstrap-Konto für Altdaten existiert, gibt seine ID zurück."""
    zeile = conn.execute(
        "SELECT id FROM benutzer WHERE benutzername = ?", (MIGRATIONS_BENUTZERNAME,)
    ).fetchone()

    if zeile:
        return zeile["id"]

    jetzt = _jetzt()
    cursor = conn.execute(
        "INSERT INTO benutzer (benutzername, email, passwort_hash, erstellt_am, aktualisiert_am, aktiv) "
        "VALUES (?, ?, ?, ?, ?, 1)",
        (
            MIGRATIONS_BENUTZERNAME,
            MIGRATIONS_EMAIL,
            auth.passwort_hash(MIGRATIONS_PASSWORT),
            jetzt,
            jetzt,
        ),
    )
    return cursor.lastrowid


def _migration_bestandsdaten_zuweisen(conn):
    """Weist eigentümerlose Alt-Dokumente/-Chats dem Migrations-Konto zu.

    Idempotent: Läuft bei jedem Start, tut aber nichts mehr, sobald
    keine Zeilen mit `user_id IS NULL` mehr existieren (z. B. bei einer
    frisch angelegten Datenbank, die von Anfang an `NOT NULL` verlangt,
    oder nachdem die Migration bereits einmal gelaufen ist).
    """
    hat_altdaten = conn.execute(
        "SELECT 1 FROM dokumente WHERE user_id IS NULL "
        "UNION SELECT 1 FROM chats WHERE user_id IS NULL LIMIT 1"
    ).fetchone()

    if not hat_altdaten:
        return

    migrations_id = _migrations_benutzer_id(conn)

    conn.execute("UPDATE dokumente SET user_id = ? WHERE user_id IS NULL", (migrations_id,))
    conn.execute("UPDATE chats SET user_id = ? WHERE user_id IS NULL", (migrations_id,))


def _dateien_migrieren():
    """Verschiebt Originaldateien aus dem alten, gemeinsamen `pdfs/`-Ordner
    in die neue Pro-Benutzer-Struktur (`users/<id>/documents/`).

    Nutzt die inzwischen (in `dokumente` gepflegte) `user_id`, um jede
    Datei ihrem tatsächlichen Eigentümer zuzuordnen. Bewusst tolerant:
    fehlt eine erwartete Datei bereits (z. B. weil die Migration
    unterbrochen und erneut gestartet wurde), wird sie übersprungen
    statt die App am Start zu hindern.
    """
    if not _ALTER_PDF_ORDNER.exists():
        return

    with _verbindung() as conn:
        zeilen = conn.execute(
            "SELECT hash, dateityp, user_id FROM dokumente WHERE user_id IS NOT NULL"
        ).fetchall()

    for zeile in zeilen:
        dateityp = zeile["dateityp"] or "pdf"
        alte_datei = _ALTER_PDF_ORDNER / f"{zeile['hash']}.{dateityp}"

        if not alte_datei.exists():
            continue

        neuer_ordner = _benutzer_dokumente_ordner(zeile["user_id"])
        neue_datei = neuer_ordner / alte_datei.name

        if not neue_datei.exists():
            alte_datei.replace(neue_datei)

    # Leeren Alt-Ordner aufräumen, falls jetzt vollständig migriert.
    try:
        next(_ALTER_PDF_ORDNER.iterdir())
    except StopIteration:
        _ALTER_PDF_ORDNER.rmdir()
    except FileNotFoundError:
        pass


def datenbank_initialisieren():
    """Legt benötigte Tabellen an, ergänzt fehlende Spalten und migriert Altdaten.

    Reihenfolge ist bewusst: (1) Tabellen inkl. `benutzer` frisch anlegen
    (wirkt nur bei einer brandneuen Datenbank), (2) fehlende Spalten
    additiv ergänzen (wirkt nur bei einer bestehenden Alt-Datenbank vor
    der Mehrbenutzer- bzw. Konto-/Sicherheits-Umstellung), (3)
    eigentümerlose Alt-Zeilen einem Bootstrap-Konto zuweisen, (4)
    `dokumente` bei Bedarf mit zusammengesetzter Eindeutigkeit neu
    aufbauen, (5) eine dadurch (bei vor diesem Fix migrierten
    Datenbanken) defekt gewordene `chunks`-Fremdschlüssel-Referenz
    reparieren, (6) `chats.user_id` bei Bedarf mit echter
    `ON DELETE CASCADE`-Kaskade neu aufbauen (Voraussetzung für
    `konto_endgueltig_loeschen`, siehe `_chats_tabelle_neu_aufbauen`),
    (7) Originaldateien in die Pro-Benutzer-Ordnerstruktur verschieben.
    Jeder Schritt ist für sich idempotent - beliebig oft ausführbar,
    ohne bestehende Daten zu verlieren oder zu duplizieren.
    """
    with _verbindung() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS benutzer (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                benutzername TEXT NOT NULL UNIQUE,
                email TEXT NOT NULL UNIQUE,
                passwort_hash TEXT NOT NULL,
                erstellt_am TEXT NOT NULL,
                aktualisiert_am TEXT NOT NULL,
                aktiv INTEGER NOT NULL DEFAULT 1,
                email_verified INTEGER NOT NULL DEFAULT 1,
                last_login_at TEXT,
                last_activity_at TEXT
            );

            CREATE TABLE IF NOT EXISTS dokumente (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES benutzer(id) ON DELETE CASCADE,
                dateiname TEXT NOT NULL,
                hash TEXT NOT NULL,
                seitenzahl INTEGER NOT NULL,
                hochgeladen_am TEXT NOT NULL,
                dateityp TEXT NOT NULL DEFAULT 'pdf',
                einheit_typ TEXT NOT NULL DEFAULT 'seite',
                groesse_bytes INTEGER,
                UNIQUE(hash, user_id)
            );

            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dokument_id INTEGER NOT NULL REFERENCES dokumente(id) ON DELETE CASCADE,
                seitennummer INTEGER NOT NULL,
                text TEXT NOT NULL,
                embedding BLOB NOT NULL,
                einheit_typ TEXT NOT NULL DEFAULT 'seite',
                einheit_anzeige TEXT
            );

            CREATE TABLE IF NOT EXISTS chats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES benutzer(id) ON DELETE CASCADE,
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

            CREATE TABLE IF NOT EXISTS email_verifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES benutzer(id) ON DELETE CASCADE,
                email TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                erstellt_am TEXT NOT NULL,
                laeuft_ab_am TEXT NOT NULL,
                verwendet_am TEXT
            );

            CREATE TABLE IF NOT EXISTS password_resets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES benutzer(id) ON DELETE CASCADE,
                token_hash TEXT NOT NULL UNIQUE,
                erstellt_am TEXT NOT NULL,
                laeuft_ab_am TEXT NOT NULL,
                verwendet_am TEXT
            );
            """
        )

        # Additive Migration für Datenbanken, die vor der Mehrformat-,
        # der Mehrbenutzer- bzw. der Konto-/Sicherheits-Umstellung
        # angelegt wurden. `user_id` wird hier bewusst NULLABLE ergänzt
        # (SQLite kann NOT NULL nicht ohne Weiteres nachträglich per ADD
        # COLUMN erzwingen) - die eigentliche Zuweisung übernimmt
        # `_migration_bestandsdaten_zuweisen`. `email_verified` bekommt
        # bewusst DEFAULT 1 (verifiziert): Es gibt noch keinen
        # angebundenen E-Mail-Versand, der eine echte Verifizierung
        # überhaupt ermöglichen würde - bestehende Entwicklungs-Konten
        # dürfen dadurch nicht ausgesperrt werden (siehe `konto.py`).
        _spalten_ergaenzen(
            conn,
            "benutzer",
            [
                ("email_verified", "INTEGER NOT NULL DEFAULT 1"),
                ("last_login_at", "TEXT"),
                ("last_activity_at", "TEXT"),
            ],
        )
        _spalten_ergaenzen(
            conn,
            "dokumente",
            [
                ("dateityp", "TEXT NOT NULL DEFAULT 'pdf'"),
                ("einheit_typ", "TEXT NOT NULL DEFAULT 'seite'"),
                ("groesse_bytes", "INTEGER"),
                ("user_id", "INTEGER REFERENCES benutzer(id)"),
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
        _spalten_ergaenzen(
            conn,
            "chats",
            [
                ("user_id", "INTEGER REFERENCES benutzer(id)"),
            ],
        )

        _migration_bestandsdaten_zuweisen(conn)
        muss_dokumente_neu_aufbauen = not _dokumente_tabelle_pro_benutzer_eindeutig(conn)

    if muss_dokumente_neu_aufbauen:
        _dokumente_tabelle_neu_aufbauen()

    # Immer (nicht nur wenn der obige Neuaufbau in diesem Lauf
    # stattfand) - bereits vor diesem Fix migrierte Datenbanken tragen
    # den Schaden dauerhaft in ihrem gespeicherten Tabellen-DDL und
    # brauchen die Reparatur bei jedem Start, bis sie einmal gelaufen
    # ist; die Funktion selbst prüft und ist ein No-Op, wenn nichts
    # defekt ist.
    _chunks_tabelle_neu_aufbauen()

    # Ebenfalls immer, aus demselben Grund - repariert eine fehlende
    # `ON DELETE CASCADE`-Kaskade auf `chats.user_id` bei Datenbanken,
    # die vor der Konto-/Sicherheits-Umstellung angelegt wurden. Muss
    # VOR jeder Nutzung von `konto_endgueltig_loeschen` gelaufen sein,
    # sonst schlägt die Kontolöschung an verbliebenen Chats fehl statt
    # sie korrekt zu kaskadieren.
    _chats_tabelle_neu_aufbauen()

    _dateien_migrieren()


def _jetzt():
    return datetime.now().isoformat(timespec="seconds")


# --- Benutzerkonten ---


def benutzer_erstellen(benutzername, email, passwort):
    """Legt ein neues Benutzerkonto an und gibt die neue Benutzer-ID zurück.

    Wirft `sqlite3.IntegrityError`, wenn Benutzername oder E-Mail schon
    vergeben sind (`UNIQUE`-Constraints) - `benutzer.py` prüft dies
    vorab bereits gezielt (für konkrete deutsche Fehlermeldungen), diese
    Funktion selbst verlässt sich aber nicht allein darauf, sondern auf
    die Datenbank-Constraints als letzte, verbindliche Schutzschicht.
    """
    jetzt = _jetzt()

    with _verbindung() as conn:
        cursor = conn.execute(
            "INSERT INTO benutzer (benutzername, email, passwort_hash, erstellt_am, aktualisiert_am, aktiv) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            (benutzername.strip(), email.strip().lower(), auth.passwort_hash(passwort), jetzt, jetzt),
        )
        return cursor.lastrowid


def benutzername_frei(benutzername, ausser_benutzer_id=None):
    """Prüft, ob ein Benutzername noch frei ist.

    `ausser_benutzer_id` schließt (falls gesetzt) genau dieses Konto von
    der Prüfung aus - nötig, damit ein Benutzer beim Bearbeiten seiner
    Kontodaten (`konto_aktualisieren`) nicht fälschlich gegen seinen
    EIGENEN, unveränderten Benutzernamen als "bereits vergeben"
    abgewiesen wird. `id IS NOT ?` mit `ausser_benutzer_id=None` wird zu
    `id IS NOT NULL`, was für jede Zeile wahr ist (IDs sind nie NULL) -
    ohne Ausschluss-ID verhält sich die Prüfung also unverändert wie
    zuvor.
    """
    with _verbindung() as conn:
        zeile = conn.execute(
            "SELECT 1 FROM benutzer WHERE benutzername = ? AND id IS NOT ?",
            (benutzername.strip(), ausser_benutzer_id),
        ).fetchone()
        return zeile is None


def email_frei(email, ausser_benutzer_id=None):
    """Prüft, ob eine E-Mail-Adresse noch frei ist (siehe `benutzername_frei`)."""
    with _verbindung() as conn:
        zeile = conn.execute(
            "SELECT 1 FROM benutzer WHERE email = ? AND id IS NOT ?",
            (email.strip().lower(), ausser_benutzer_id),
        ).fetchone()
        return zeile is None


def benutzer_nach_login(login_wert):
    """Lädt ein aktives Benutzerkonto per Benutzername ODER E-Mail (oder None)."""
    login_wert = (login_wert or "").strip()

    with _verbindung() as conn:
        zeile = conn.execute(
            "SELECT * FROM benutzer WHERE aktiv = 1 AND (benutzername = ? OR email = ?)",
            (login_wert, login_wert.lower()),
        ).fetchone()
        return dict(zeile) if zeile else None


def benutzer_nach_id(benutzer_id):
    """Kontodaten für die Sitzung - bewusst OHNE `passwort_hash`."""
    with _verbindung() as conn:
        zeile = conn.execute(
            "SELECT id, benutzername, email, email_verified, erstellt_am, "
            "aktualisiert_am, last_login_at, last_activity_at, aktiv "
            "FROM benutzer WHERE id = ?",
            (benutzer_id,),
        ).fetchone()
        return dict(zeile) if zeile else None


def benutzer_konto_daten(benutzer_id):
    """Kontodaten DIESES Benutzers für Anzeige/Export - bewusst OHNE
    `passwort_hash` und ohne die interne, numerische `id` (siehe
    `konto.py`/`datenexport.py`: interne IDs werden dem Benutzer
    grundsätzlich nicht angezeigt)."""
    with _verbindung() as conn:
        zeile = conn.execute(
            "SELECT benutzername, email, email_verified, erstellt_am, "
            "aktualisiert_am, last_login_at, last_activity_at "
            "FROM benutzer WHERE id = ?",
            (benutzer_id,),
        ).fetchone()
        return dict(zeile) if zeile else None


def konto_aktualisieren(benutzer_id, aktuelles_passwort, neuer_benutzername=None, neue_email=None):
    """Aktualisiert Benutzername und/oder E-Mail-Adresse DIESES Benutzers.

    Verlangt zwingend das aktuelle Passwort als Bestätigung, bevor
    irgendetwas geändert wird - eine gekaperte, aber noch angemeldete
    Sitzung (z. B. ein kurz unbeaufsichtigter Browser-Tab) kann Kontodaten
    dadurch nicht ohne erneuten Passwort-Nachweis manipulieren. Alle
    Prüfungen und die eigentliche Änderung laufen in EINER Transaktion.

    Gibt `(erfolg: bool, meldung: str, email_geaendert: bool)` zurück.
    Bei `email_geaendert=True` wurde `email_verified` bereits auf 0
    zurückgesetzt; die aufrufende UI-Schicht (`konto.py`) erzeugt darauf
    aufbauend einen neuen Verifizierungs-Token über
    `email_verifizierung_erstellen`.
    """
    with _verbindung() as conn:
        zeile = conn.execute(
            "SELECT passwort_hash, benutzername, email FROM benutzer WHERE id = ?",
            (benutzer_id,),
        ).fetchone()

        if not zeile:
            return False, "Konto nicht gefunden.", False

        if not auth.passwort_pruefen(aktuelles_passwort, zeile["passwort_hash"]):
            return False, "Das aktuelle Passwort ist falsch.", False

        neuer_benutzername = (neuer_benutzername or zeile["benutzername"]).strip()
        neue_email = (neue_email or zeile["email"]).strip().lower()

        if not auth.benutzername_gueltig(neuer_benutzername):
            return False, (
                "Der Benutzername darf nur Buchstaben, Ziffern, „_“, „.“ "
                "oder „-“ enthalten (3–32 Zeichen)."
            ), False

        if not auth.email_gueltig(neue_email):
            return False, "Bitte gib eine gültige E-Mail-Adresse ein.", False

        if conn.execute(
            "SELECT 1 FROM benutzer WHERE benutzername = ? AND id IS NOT ?",
            (neuer_benutzername, benutzer_id),
        ).fetchone():
            return False, "Dieser Benutzername ist bereits vergeben.", False

        if conn.execute(
            "SELECT 1 FROM benutzer WHERE email = ? AND id IS NOT ?",
            (neue_email, benutzer_id),
        ).fetchone():
            return False, "Diese E-Mail-Adresse wird bereits verwendet.", False

        email_geaendert = neue_email != zeile["email"]
        jetzt = _jetzt()

        if email_geaendert:
            conn.execute(
                "UPDATE benutzer SET benutzername = ?, email = ?, "
                "email_verified = 0, aktualisiert_am = ? WHERE id = ?",
                (neuer_benutzername, neue_email, jetzt, benutzer_id),
            )
        else:
            conn.execute(
                "UPDATE benutzer SET benutzername = ?, aktualisiert_am = ? WHERE id = ?",
                (neuer_benutzername, jetzt, benutzer_id),
            )

    return True, "Deine Kontodaten wurden aktualisiert.", email_geaendert


def passwort_aendern(benutzer_id, aktuelles_passwort, neues_passwort):
    """Ändert das Passwort DIESES Benutzers nach Prüfung des aktuellen Passworts.

    Gibt `(erfolg: bool, meldung: str)` zurück. Das neue Passwort wird
    ausschließlich als Argon2id-Hash gespeichert (`auth.passwort_hash`) -
    weder das alte noch das neue Klartext-Passwort werden an irgendeiner
    Stelle geloggt oder in einer Ausnahme mitgegeben.
    """
    with _verbindung() as conn:
        zeile = conn.execute(
            "SELECT passwort_hash FROM benutzer WHERE id = ?", (benutzer_id,)
        ).fetchone()

        if not zeile:
            return False, "Konto nicht gefunden."

        if not auth.passwort_pruefen(aktuelles_passwort, zeile["passwort_hash"]):
            return False, "Das aktuelle Passwort ist falsch."

        if not auth.passwort_stark_genug(neues_passwort):
            return False, (
                f"Das neue Passwort muss mindestens "
                f"{auth.MINDEST_PASSWORT_LAENGE} Zeichen enthalten."
            )

        conn.execute(
            "UPDATE benutzer SET passwort_hash = ?, aktualisiert_am = ? WHERE id = ?",
            (auth.passwort_hash(neues_passwort), _jetzt(), benutzer_id),
        )

    return True, "Dein Passwort wurde erfolgreich geändert."


def letzten_login_aktualisieren(benutzer_id):
    """Setzt `last_login_at` bei jedem erfolgreichen Login - siehe `benutzer._login_versuchen`."""
    with _verbindung() as conn:
        conn.execute(
            "UPDATE benutzer SET last_login_at = ? WHERE id = ?", (_jetzt(), benutzer_id)
        )


def letzte_aktivitaet_aktualisieren(benutzer_id):
    """Setzt `last_activity_at` - NUR bei inhaltlicher Aktivität aufzurufen
    (Login, Upload, Chat-Frage, Analyse, Prüfung), NIEMALS bei jedem
    Streamlit-Rerun (siehe Aufrufstellen in `web_app.py`) - Grundlage für
    eine künftige Inaktivitäts-Richtlinie, die noch nicht automatisch
    durchgesetzt wird.
    """
    with _verbindung() as conn:
        conn.execute(
            "UPDATE benutzer SET last_activity_at = ? WHERE id = ?", (_jetzt(), benutzer_id)
        )


def _token_hash(roher_token):
    """SHA-256 des Klartext-Tokens - gespeichert wird ausschließlich dieser
    Hash, nie der Token selbst (siehe `email_verifications`/`password_resets`).
    Ein einfacher, ungesalzener Hash genügt hier bewusst (anders als bei
    Passwörtern): der Token selbst ist bereits ein kryptographisch
    zufälliger, hochentropischer Wert (`secrets.token_urlsafe`), kein vom
    Menschen gewähltes, erratbares Geheimnis - ein Offline-Wörterbuch-
    angriff auf den Hash ist damit praktisch aussichtslos.
    """
    return hashlib.sha256(roher_token.encode("utf-8")).hexdigest()


def email_verifizierung_erstellen(benutzer_id, email):
    """Erzeugt einen neuen E-Mail-Verifizierungs-Token für `email` und gibt
    ihn im KLARTEXT zurück (einzige Gelegenheit - gespeichert wird nur
    sein Hash). Macht zuerst alle noch nicht eingelösten Tokens dieses
    Benutzers ungültig ("invalidate older tokens"), bevor ein neuer
    erzeugt wird, damit nie mehrere gleichzeitig gültige Links existieren.
    """
    roher_token = secrets.token_urlsafe(32)
    laeuft_ab = (
        datetime.now() + timedelta(hours=EMAIL_VERIFIZIERUNG_GUELTIGKEIT_STUNDEN)
    ).isoformat(timespec="seconds")

    with _verbindung() as conn:
        conn.execute(
            "UPDATE email_verifications SET verwendet_am = ? "
            "WHERE user_id = ? AND verwendet_am IS NULL",
            (_jetzt(), benutzer_id),
        )
        conn.execute(
            "INSERT INTO email_verifications "
            "(user_id, email, token_hash, erstellt_am, laeuft_ab_am) "
            "VALUES (?, ?, ?, ?, ?)",
            (benutzer_id, email, _token_hash(roher_token), _jetzt(), laeuft_ab),
        )

    return roher_token


def email_verifizierung_bestaetigen(roher_token):
    """Löst einen E-Mail-Verifizierungs-Token ein (einmalig, mit Ablauf).

    Gibt `(erfolg: bool, meldung: str)` zurück. Setzt `email_verified`
    nur, wenn die im Token hinterlegte E-Mail noch mit der AKTUELLEN
    E-Mail des Kontos übereinstimmt - verhindert, dass ein alter Link zu
    einer inzwischen erneut geänderten Adresse noch etwas bewirkt.
    """
    zeile_gefunden = None

    with _verbindung() as conn:
        zeile = conn.execute(
            "SELECT id, user_id, email, laeuft_ab_am FROM email_verifications "
            "WHERE token_hash = ? AND verwendet_am IS NULL",
            (_token_hash(roher_token),),
        ).fetchone()

        if not zeile:
            return False, "Dieser Bestätigungslink ist ungültig oder wurde bereits verwendet."

        if datetime.fromisoformat(zeile["laeuft_ab_am"]) < datetime.now():
            return False, "Dieser Bestätigungslink ist abgelaufen."

        conn.execute(
            "UPDATE email_verifications SET verwendet_am = ? WHERE id = ?",
            (_jetzt(), zeile["id"]),
        )
        conn.execute(
            "UPDATE benutzer SET email_verified = 1 WHERE id = ? AND email = ?",
            (zeile["user_id"], zeile["email"]),
        )
        zeile_gefunden = zeile

    return True, "Deine E-Mail-Adresse wurde bestätigt."


def passwort_reset_anfordern(email_oder_benutzername):
    """Erzeugt (falls ein aktives Konto zu dieser Login-Kennung existiert)
    einen neuen Passwort-Reset-Token und gibt ihn im KLARTEXT zurück -
    gespeichert wird ausschließlich sein Hash. Ältere, noch nicht
    eingelöste Reset-Tokens desselben Benutzers werden zuvor ungültig
    gemacht.

    Gibt `None` zurück, wenn keine passende Login-Kennung existiert -
    bewusst OHNE unterscheidbare Fehlermeldung an den Aufrufer, damit
    sich daraus nicht ableiten lässt, ob ein Konto zu dieser E-Mail/
    diesem Benutzernamen existiert (Schutz vor User-Enumeration).
    """
    benutzer = benutzer_nach_login(email_oder_benutzername)

    if not benutzer:
        return None

    roher_token = secrets.token_urlsafe(32)
    laeuft_ab = (
        datetime.now() + timedelta(hours=PASSWORT_RESET_GUELTIGKEIT_STUNDEN)
    ).isoformat(timespec="seconds")

    with _verbindung() as conn:
        conn.execute(
            "UPDATE password_resets SET verwendet_am = ? "
            "WHERE user_id = ? AND verwendet_am IS NULL",
            (_jetzt(), benutzer["id"]),
        )
        conn.execute(
            "INSERT INTO password_resets (user_id, token_hash, erstellt_am, laeuft_ab_am) "
            "VALUES (?, ?, ?, ?)",
            (benutzer["id"], _token_hash(roher_token), _jetzt(), laeuft_ab),
        )

    return roher_token


def passwort_reset_einloesen(roher_token, neues_passwort):
    """Löst einen Passwort-Reset-Token ein und setzt das neue Passwort.

    Gibt `(erfolg: bool, meldung: str)` zurück. Einmalig verwendbar
    (`verwendet_am` wird sofort gesetzt) und zeitlich begrenzt gültig.
    """
    if not auth.passwort_stark_genug(neues_passwort):
        return False, (
            f"Das neue Passwort muss mindestens "
            f"{auth.MINDEST_PASSWORT_LAENGE} Zeichen enthalten."
        )

    with _verbindung() as conn:
        zeile = conn.execute(
            "SELECT id, user_id, laeuft_ab_am FROM password_resets "
            "WHERE token_hash = ? AND verwendet_am IS NULL",
            (_token_hash(roher_token),),
        ).fetchone()

        if not zeile:
            return False, "Dieser Link ist ungültig oder wurde bereits verwendet."

        if datetime.fromisoformat(zeile["laeuft_ab_am"]) < datetime.now():
            return False, "Dieser Link ist abgelaufen."

        conn.execute(
            "UPDATE benutzer SET passwort_hash = ?, aktualisiert_am = ? WHERE id = ?",
            (auth.passwort_hash(neues_passwort), _jetzt(), zeile["user_id"]),
        )
        conn.execute(
            "UPDATE password_resets SET verwendet_am = ? WHERE id = ?",
            (_jetzt(), zeile["id"]),
        )

    return True, "Dein Passwort wurde zurückgesetzt."


def konto_passwort_gueltig(benutzer_id, passwort):
    """Prüft ein Klartext-Passwort gegen den gespeicherten Hash DIESES
    Benutzers, OHNE etwas zu ändern - für sicherheitsrelevante
    Bestätigungen (z. B. vor einer Kontolöschung), die selbst keine
    eigene Datenänderung vornehmen und deshalb nicht über
    `konto_aktualisieren`/`passwort_aendern` laufen.
    """
    with _verbindung() as conn:
        zeile = conn.execute(
            "SELECT passwort_hash FROM benutzer WHERE id = ?", (benutzer_id,)
        ).fetchone()

    if not zeile:
        return False

    return auth.passwort_pruefen(passwort, zeile["passwort_hash"])


def konto_endgueltig_loeschen(benutzer_id):
    """Löscht dieses Benutzerkonto und ALLE seine Daten unwiderruflich.

    Löscht ausschließlich die `benutzer`-Zeile in einer einzigen
    Transaktion - dank durchgängiger `ON DELETE CASCADE`-Fremdschlüssel
    (`dokumente`, `chats` -> `chunks`/`nachrichten`, `email_verifications`,
    `password_resets`, siehe `datenbank_initialisieren`) entfernt SQLite
    dabei automatisch und atomar JEDE abhängige Zeile dieses Benutzers;
    `PRAGMA foreign_keys = ON` ist in `_verbindung()` für jede Verbindung
    aktiv, ohne das würde SQLite weder Fremdschlüssel durchsetzen noch
    kaskadieren. Setzt voraus, dass `_chats_tabelle_neu_aufbauen` bereits
    einmal gelaufen ist (immer der Fall, siehe `datenbank_initialisieren`)
    - sonst bricht das Löschen an verbliebenen Chats mit einem
    Fremdschlüssel-Fehler ab, STATT sie zu kaskadieren, und das Konto
    bliebe (korrekt) erhalten statt inkonsistent halb gelöscht zu werden.

    Die Originaldateien auf der Festplatte liegen außerhalb der
    Datenbank und werden deshalb bewusst ERST NACH erfolgreichem Commit
    entfernt: Schlägt der DB-Teil fehl, bleiben die Dateien unangetastet
    statt verwaist referenziert zu werden.

    Gibt `None` bei vollem Erfolg zurück, sonst eine Fehlermeldung zur
    (unvollständigen) Dateisystem-Bereinigung - das Konto selbst ist in
    jedem Fall bereits gelöscht (kein Login mehr möglich, keine
    hängenden Fremdschlüssel in der Datenbank).
    """
    with _verbindung() as conn:
        conn.execute("DELETE FROM benutzer WHERE id = ?", (benutzer_id,))

    benutzer_ordner = BENUTZER_ORDNER / str(int(benutzer_id))

    if not benutzer_ordner.exists():
        return None

    try:
        shutil.rmtree(benutzer_ordner)
    except OSError as fehler:
        return str(fehler)

    return None


def dokument_datei_lesen(dokument_id, benutzer_id):
    """Liest die Original-Datei eines Dokuments als Bytes, NUR wenn es
    tatsächlich diesem Benutzer gehört (sonst `None`) - genutzt vom
    Datenexport (`datenexport.py`), damit dessen ZIP-Aufbau nie selbst
    einen Dateipfad aus einer möglicherweise fremden `dokument_id`
    konstruieren muss, sondern die Eigentümerprüfung immer über diese
    Funktion läuft.
    """
    with _verbindung() as conn:
        zeile = conn.execute(
            "SELECT hash, dateityp FROM dokumente WHERE id = ? AND user_id = ?",
            (dokument_id, benutzer_id),
        ).fetchone()

    if not zeile:
        return None

    dateityp = zeile["dateityp"] or "pdf"
    pfad = _benutzer_dokumente_ordner(benutzer_id) / f"{zeile['hash']}.{dateityp}"

    if not pfad.exists():
        return None

    return pfad.read_bytes()


# --- Dokumentbibliothek ---


def _benutzer_dokumente_ordner(benutzer_id):
    """Eigener Ablageordner je Benutzer für Original-Dateikopien.

    `benutzer_id` ist immer eine interne Ganzzahl aus der eigenen
    Datenbank (nie ein von außen übergebener String) - der Pfad ist
    dadurch inhärent sicher vor Path-Traversal, ganz ohne zusätzliche
    Sanitisierung des Wertes selbst.
    """
    ordner = BENUTZER_ORDNER / str(int(benutzer_id)) / "documents"
    ordner.mkdir(parents=True, exist_ok=True)
    return ordner


def hash_berechnen(datei_bytes):
    return hashlib.sha256(datei_bytes).hexdigest()


def dokument_nach_hash(hash_wert, benutzer_id):
    """Gibt das gespeicherte Dokument DIESES Benutzers mit diesem Datei-Hash zurück (oder None).

    Bewusst nach `benutzer_id` gefiltert (nicht nur nach `hash`): der
    Duplikat-Schutz beim Upload gilt je Benutzer, nicht global - sonst
    könnte ein Benutzer, dessen Datei zufällig byte-identisch mit der
    eines anderen Benutzers ist, sein Dokument nicht mehr hochladen
    bzw. der Check würde fälschlich das fremde Dokument "finden".
    """
    with _verbindung() as conn:
        zeile = conn.execute(
            "SELECT * FROM dokumente WHERE hash = ? AND user_id = ?",
            (hash_wert, benutzer_id),
        ).fetchone()
        return dict(zeile) if zeile else None


def dokument_speichern(dateiname, hash_wert, datei_bytes, einheiten_anzahl, dateityp, einheit_typ, benutzer_id):
    """Speichert eine Dokumentdatei + Metadaten für den angegebenen Benutzer.

    `einheiten_anzahl`/`einheit_typ` sind formatunabhängig zu verstehen:
    Seiten bei PDF, Folien bei PPTX, Tabellenblätter bei XLSX,
    Abschnitte bei DOCX/TXT/MD/CSV (siehe `dokument_verarbeitung.py`).
    Die Originaldatei landet ausschließlich im Ordner dieses Benutzers
    (`_benutzer_dokumente_ordner`), benannt nach Hash + Dateityp - nie
    nach dem (nutzergesteuerten) Original-Dateinamen.
    """
    with _verbindung() as conn:
        cursor = conn.execute(
            "INSERT INTO dokumente "
            "(user_id, dateiname, hash, seitenzahl, hochgeladen_am, dateityp, einheit_typ, groesse_bytes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                benutzer_id,
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

    (_benutzer_dokumente_ordner(benutzer_id) / f"{hash_wert}.{dateityp}").write_bytes(datei_bytes)

    return dokument_id


def chunks_speichern(dokument_id, chunks, embeddings):
    """Speichert Chunks inkl. Embeddings zu einem Dokument.

    Kein eigener `benutzer_id`-Parameter nötig: `dokument_id` stammt
    stets direkt aus dem vorangegangenen `dokument_speichern`-Aufruf
    (derselbe Upload-Vorgang), Eigentümerschaft ist also bereits über
    das referenzierte Dokument gegeben.
    """
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


def dokumente_laden(benutzer_id):
    """Gibt alle Dokumente DIESES Benutzers zurück, neueste zuerst."""
    with _verbindung() as conn:
        zeilen = conn.execute(
            "SELECT * FROM dokumente WHERE user_id = ? ORDER BY hochgeladen_am DESC",
            (benutzer_id,),
        ).fetchall()
        return [dict(zeile) for zeile in zeilen]


def dokument_loeschen(dokument_id, benutzer_id):
    """Entfernt ein Dokument samt Chunks (Kaskade) und seiner Dateikopie -
    NUR, wenn es tatsächlich dem angegebenen Benutzer gehört.

    Gehört die ID keinem Dokument dieses Benutzers (falsche ID, fremdes
    Dokument, bereits gelöscht), passiert schlicht nichts - kein Fehler,
    aber auch keine Löschung. Bereinigt außerdem aktiv die Referenz auf
    diese Dokument-ID in `chats.dokument_ids`, beschränkt auf die Chats
    desselben Benutzers (fremde Chats können diese ID ohnehin nie
    enthalten haben, da die Auswahl in der UI stets auf eigene Dokumente
    begrenzt ist - die Einschränkung hier ist zusätzliche Absicherung).
    """
    with _verbindung() as conn:
        zeile = conn.execute(
            "SELECT hash, dateityp FROM dokumente WHERE id = ? AND user_id = ?",
            (dokument_id, benutzer_id),
        ).fetchone()

        if not zeile:
            return

        conn.execute("DELETE FROM dokumente WHERE id = ? AND user_id = ?", (dokument_id, benutzer_id))

        for chat_zeile in conn.execute(
            "SELECT id, dokument_ids FROM chats WHERE user_id = ?", (benutzer_id,)
        ).fetchall():
            vorhandene_ids = json.loads(chat_zeile["dokument_ids"])

            if dokument_id in vorhandene_ids:
                bereinigte_ids = [i for i in vorhandene_ids if i != dokument_id]
                conn.execute(
                    "UPDATE chats SET dokument_ids = ? WHERE id = ?",
                    (json.dumps(bereinigte_ids), chat_zeile["id"]),
                )

    dateityp = zeile["dateityp"] or "pdf"
    (_benutzer_dokumente_ordner(benutzer_id) / f"{zeile['hash']}.{dateityp}").unlink(missing_ok=True)


def chunks_laden(dokument_ids, benutzer_id):
    """Lädt Chunks (inkl. Embedding als numpy-Array) der übergebenen Dokumente.

    Der JOIN gegen `dokumente` mit `AND d.user_id = ?` ist die
    entscheidende Sicherheitsgrenze dieser Funktion: IDs in
    `dokument_ids`, die nicht (auch) dem angegebenen Benutzer gehören,
    liefern für diese ID einfach keine Treffer - unabhängig davon, ob
    `dokument_ids` aus einer vertrauenswürdigen UI-Auswahl stammt oder
    (versehentlich oder absichtlich) fremde IDs enthält.
    """
    if not dokument_ids:
        return []

    platzhalter = ", ".join("?" for _ in dokument_ids)

    with _verbindung() as conn:
        zeilen = conn.execute(
            f"SELECT c.text, c.seitennummer, c.embedding, c.einheit_typ, "
            f"c.einheit_anzeige, d.dateiname "
            f"FROM chunks c JOIN dokumente d ON d.id = c.dokument_id "
            f"WHERE c.dokument_id IN ({platzhalter}) AND d.user_id = ?",
            (*dokument_ids, benutzer_id),
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


def chat_erstellen(benutzer_id, titel=STANDARD_CHAT_TITEL):
    with _verbindung() as conn:
        jetzt = _jetzt()
        cursor = conn.execute(
            "INSERT INTO chats (user_id, titel, erstellt_am, aktualisiert_am, dokument_ids) "
            "VALUES (?, ?, ?, ?, '[]')",
            (benutzer_id, titel, jetzt, jetzt),
        )
        return cursor.lastrowid


def chat_liste(benutzer_id):
    """Gibt alle Chats DIESES Benutzers zurück (ohne Nachrichten), zuletzt aktualisiert zuerst."""
    with _verbindung() as conn:
        zeilen = conn.execute(
            "SELECT id, titel, erstellt_am, aktualisiert_am FROM chats "
            "WHERE user_id = ? ORDER BY aktualisiert_am DESC",
            (benutzer_id,),
        ).fetchall()
        return [dict(zeile) for zeile in zeilen]


def _existierende_dokument_ids(conn, dokument_ids, benutzer_id):
    if not dokument_ids:
        return []

    platzhalter = ", ".join("?" for _ in dokument_ids)
    zeilen = conn.execute(
        f"SELECT id FROM dokumente WHERE id IN ({platzhalter}) AND user_id = ?",
        (*dokument_ids, benutzer_id),
    ).fetchall()
    vorhandene_ids = {zeile["id"] for zeile in zeilen}

    return [i for i in dokument_ids if i in vorhandene_ids]


def chat_laden(chat_id, benutzer_id):
    """Lädt einen Chat DIESES Benutzers inkl. Nachrichten, oder None.

    Gehört `chat_id` keinem Chat dieses Benutzers, wird `None`
    zurückgegeben - identisch zum "existiert nicht"-Fall, damit sich
    aus der Antwort nicht ableiten lässt, ob die ID zu einem fremden
    Chat gehört oder schlicht nicht existiert. `dokument_ids` wird
    zusätzlich auf noch existierende UND weiterhin diesem Benutzer
    gehörende Dokumente gefiltert.
    """
    with _verbindung() as conn:
        chat_zeile = conn.execute(
            "SELECT * FROM chats WHERE id = ? AND user_id = ?", (chat_id, benutzer_id)
        ).fetchone()

        if not chat_zeile:
            return None

        nachrichten_zeilen = conn.execute(
            "SELECT * FROM nachrichten WHERE chat_id = ? ORDER BY id",
            (chat_id,),
        ).fetchall()

        dokument_ids = _existierende_dokument_ids(
            conn, json.loads(chat_zeile["dokument_ids"]), benutzer_id
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


def chat_dokumente_setzen(chat_id, dokument_ids, benutzer_id):
    """Setzt die aktiven Dokumente eines Chats - nur für Chat UND Dokumente des Benutzers.

    `dokument_ids` wird vor dem Speichern auf tatsächlich existierende,
    dem Benutzer gehörende Dokumente gefiltert - selbst wenn die
    aufrufende UI (die nur eigene Dokumente zur Auswahl anbietet)
    fehlerhaft fremde IDs übergeben würde, könnten diese nie in einem
    Chat landen.
    """
    with _verbindung() as conn:
        gehoert_dem_benutzer = conn.execute(
            "SELECT 1 FROM chats WHERE id = ? AND user_id = ?", (chat_id, benutzer_id)
        ).fetchone()

        if not gehoert_dem_benutzer:
            return

        gueltige_ids = _existierende_dokument_ids(conn, list(dokument_ids), benutzer_id)

        conn.execute(
            "UPDATE chats SET dokument_ids = ? WHERE id = ? AND user_id = ?",
            (json.dumps(gueltige_ids), chat_id, benutzer_id),
        )


def chat_loeschen(chat_id, benutzer_id):
    with _verbindung() as conn:
        conn.execute("DELETE FROM chats WHERE id = ? AND user_id = ?", (chat_id, benutzer_id))


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


def nachricht_hinzufuegen(chat_id, benutzer_id, frage, antwort, quellen):
    """Speichert eine Chatrunde und aktualisiert Zeitstempel/Titel des Chats.

    Prüft zuerst, ob `chat_id` tatsächlich diesem Benutzer gehört -
    ohne diese Prüfung könnte eine manipulierte `chat_id` sonst eine
    Nachricht in einen fremden Chat schreiben. Der Titel wird nur bei
    der ersten Nachricht eines Chats automatisch aus der Frage
    abgeleitet (und nur, wenn er noch der Standardtitel ist).
    """
    jetzt = _jetzt()

    with _verbindung() as conn:
        gehoert_dem_benutzer = conn.execute(
            "SELECT titel FROM chats WHERE id = ? AND user_id = ?", (chat_id, benutzer_id)
        ).fetchone()

        if not gehoert_dem_benutzer:
            raise PermissionError("Dieser Chat gehört nicht zum angemeldeten Benutzer.")

        conn.execute(
            "INSERT INTO nachrichten (chat_id, frage, antwort, quellen, erstellt_am) "
            "VALUES (?, ?, ?, ?, ?)",
            (chat_id, frage, antwort, json.dumps(quellen), jetzt),
        )

        anzahl_zeile = conn.execute(
            "SELECT COUNT(*) AS anzahl FROM nachrichten WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()

        neuer_titel = gehoert_dem_benutzer["titel"]

        if anzahl_zeile["anzahl"] == 1 and neuer_titel == STANDARD_CHAT_TITEL:
            neuer_titel = _kurztitel_erzeugen(frage)

        conn.execute(
            "UPDATE chats SET aktualisiert_am = ?, titel = ? WHERE id = ? AND user_id = ?",
            (jetzt, neuer_titel, chat_id, benutzer_id),
        )
