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
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

import auth
import db_backend
import produkte
import storage
import zwei_faktor_krypto


# Gültigkeitsdauer sicherheitsrelevanter Einmal-Tokens (siehe
# `email_verifizierung_erstellen`/`passwort_reset_anfordern`) - bewusst
# als Konstanten statt Magic Numbers, damit sie an einer Stelle
# nachvollziehbar und leicht anpassbar sind.
EMAIL_VERIFIZIERUNG_GUELTIGKEIT_STUNDEN = 24
PASSWORT_RESET_GUELTIGKEIT_STUNDEN = 1

# Sitzungs-Richtlinie (siehe `sitzung_erstellen`/`sitzung_pruefen_und_aktualisieren`):
# eine serverseitige Sitzung ist spätestens nach `SITZUNG_MAX_LEBENSDAUER_STUNDEN`
# absolut abgelaufen (unabhängig von Aktivität), UND wird vorher schon
# ungültig, wenn seit `SITZUNG_INAKTIVITAET_MINUTEN` keine Aktivität mehr
# registriert wurde (gleitendes Fenster, bei jeder Prüfung erneuert). Werte
# bewusst moderat gewählt für eine Anwendung mit potenziell sensiblen
# Dokumenten, ohne bei normaler Nutzung (eine Arbeitssitzung) störend zu wirken.
SITZUNG_MAX_LEBENSDAUER_STUNDEN = 12
SITZUNG_INAKTIVITAET_MINUTEN = 60

# Zwei-Faktor-Authentifizierung (TOTP, siehe `zwei_faktor_krypto.py`):
# ein PENDING-Secret (während der Einrichtung, noch nicht bestätigt) wird
# nach `PENDING_2FA_GUELTIGKEIT_MINUTEN` verworfen, wenn der Nutzer das
# Setup nicht abschließt - kein halb aktiviertes 2FA bleibt unbegrenzt
# lange bestehen. Eine Login-Challenge (nach korrektem Passwort, vor
# vollständiger Anmeldung) ist nur `ZWEI_FAKTOR_CHALLENGE_GUELTIGKEIT_MINUTEN`
# gültig und wird nach `ZWEI_FAKTOR_CHALLENGE_MAX_FEHLVERSUCHE` falschen
# Codes endgültig beendet (ein neuer normaler Login ist dann erforderlich).
PENDING_2FA_GUELTIGKEIT_MINUTEN = 15
ZWEI_FAKTOR_CHALLENGE_GUELTIGKEIT_MINUTEN = 10
ZWEI_FAKTOR_CHALLENGE_MAX_FEHLVERSUCHE = 5
BACKUP_CODES_ANZAHL = 10

# Produktzugriffs-Status (siehe `produkt_zugriffe`-Tabelle/Abschnitt
# "Produktzugriffe" unten). Nur "aktiv" gewährt tatsächlichen Zugriff
# (siehe `produkt_zugriff_aktiv`) - die anderen beiden existieren bereits
# jetzt im Schema, damit ein künftiges Sperren/Deaktivieren (z. B. bei
# einer ausbleibenden Zahlung) ohne weitere Schemaänderung möglich ist,
# auch wenn in diesem Architekturblock noch nichts diese Status setzt.
PRODUKT_STATUS_AKTIV = "aktiv"
PRODUKT_STATUS_GESPERRT = "gesperrt"
PRODUKT_STATUS_DEAKTIVIERT = "deaktiviert"


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


def _verbindung():
    """Liefert eine Datenbankverbindung über die zentrale
    `db_backend`-Abstraktion (siehe dortigen Moduldocstring) - für das
    aktuell einzig unterstützte Backend `sqlite` (Standard) identisch
    zum bisherigen, direkt hier implementierten Verbindungsaufbau,
    lediglich nach `db_backend.sqlite_verbindung` verschoben, damit es
    nur eine Implementierung gibt."""
    APP_DATEN_ORDNER.mkdir(exist_ok=True)
    return db_backend.verbindung(DB_PFAD)


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
                public_id TEXT,
                storage_key TEXT,
                UNIQUE(hash, user_id)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO dokumente
                (id, user_id, dateiname, hash, seitenzahl, hochgeladen_am,
                 dateityp, einheit_typ, groesse_bytes, public_id, storage_key)
            SELECT id, user_id, dateiname, hash, seitenzahl, hochgeladen_am,
                   dateityp, einheit_typ, groesse_bytes, public_id, storage_key
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


def _dokumente_public_ids_ergaenzen(conn):
    """Stattet jedes bestehende Dokument OHNE `public_id` idempotent mit
    einer stabilen, nicht erratbaren UUID aus (siehe CLAUDE.md "Zentrale
    Dokument-ID").

    Läuft bei jedem Start; sobald jedes Dokument eine `public_id` hat,
    findet die SELECT-Abfrage nichts mehr und die Funktion ist ein No-Op.
    Neue Dokumente erhalten ihre `public_id` bereits direkt bei der
    Erstellung (siehe `dokument_speichern`) - dieser Pfad betrifft nur
    Alt-Datenbanken von vor Einführung dieser Spalte. Die `public_id`
    ist NIE eine eigenständige Zugriffsgrundlage: jede Funktion, die sie
    entgegennimmt (siehe `dokument_nach_public_id`), prüft weiterhin
    zwingend die Eigentümerschaft (`user_id`) - die UUID macht ein
    Dokument nur produktübergreifend identifizierbar, nicht zugreifbar.
    """
    zeilen = conn.execute("SELECT id FROM dokumente WHERE public_id IS NULL").fetchall()

    for zeile in zeilen:
        conn.execute(
            "UPDATE dokumente SET public_id = ? WHERE id = ?",
            (str(uuid.uuid4()), zeile["id"]),
        )

    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_dokumente_public_id "
        "ON dokumente(public_id) WHERE public_id IS NOT NULL"
    )


def _dokumente_storage_keys_ergaenzen(conn):
    """Stattet jedes bestehende Dokument OHNE `storage_key` idempotent
    mit einem Storage-Key aus, der auf seinen TATSÄCHLICHEN, bereits
    bestehenden Ablageort abbildet (siehe CLAUDE.md "Storage-Key statt
    öffentlicher Dateipfad").

    WICHTIG: verschiebt KEINE Datei - Alt-Dokumente liegen bereits unter
    `<APP_DATEN_ORDNER>/users/<user_id>/documents/<hash>.<dateityp>`
    (siehe `_benutzer_dokumente_ordner`); der hier gesetzte Storage-Key
    beschreibt exakt diesen Pfad relativ zu `APP_DATEN_ORDNER`, sodass
    `LocalFileStorage` (Basisordner = `APP_DATEN_ORDNER`) die Datei ohne
    jede Verschiebung wiederfindet. NEUE Dokumente bekommen ab
    `dokument_speichern` einen andersartigen, feingranulareren Key
    (`users/<user_id>/documents/<public_id>/original.<endung>`) - beide
    Formen sind als reiner, opaker Wert in `storage_key` gleichermaßen
    gültig, es muss nie ein einheitliches Format über alle Zeilen
    hinweg herrschen.
    """
    zeilen = conn.execute(
        "SELECT id, user_id, hash, dateityp FROM dokumente WHERE storage_key IS NULL"
    ).fetchall()

    for zeile in zeilen:
        dateityp = zeile["dateityp"] or "pdf"
        key = f"users/{zeile['user_id']}/documents/{zeile['hash']}.{dateityp}"
        conn.execute("UPDATE dokumente SET storage_key = ? WHERE id = ?", (key, zeile["id"]))


def _produktzugriffe_migrieren(conn):
    """Gewährt jedem bestehenden Benutzer OHNE Zugriffszeile automatisch
    aktiven Zugriff auf Clevoriq Documents (siehe CLAUDE.md "Produktsystem").

    Idempotent: ein Benutzer, der bereits (aus welchem Grund auch immer,
    z. B. eine künftige manuelle Sperre) eine Zeile für `documents` hat,
    wird hier NICHT angefasst - nur wer noch GAR KEINE Zeile für dieses
    Produkt hat, bekommt eine neue mit Status "aktiv". Neue Benutzer
    bekommen ihren Zugriff bereits direkt bei der Registrierung (siehe
    `benutzer_erstellen`) - dieser Pfad deckt ausschließlich Alt-Konten ab,
    die vor Einführung des Produktzugriffsmodells angelegt wurden.
    """
    jetzt = _jetzt()

    fehlende_benutzer = conn.execute(
        "SELECT b.id FROM benutzer b "
        "LEFT JOIN produkt_zugriffe p ON p.user_id = b.id AND p.product_key = ? "
        "WHERE p.id IS NULL",
        (produkte.PRODUKT_DOCUMENTS,),
    ).fetchall()

    for zeile in fehlende_benutzer:
        conn.execute(
            "INSERT INTO produkt_zugriffe "
            "(user_id, product_key, status, plan, aktiviert_am) "
            "VALUES (?, ?, ?, ?, ?)",
            (zeile["id"], produkte.PRODUKT_DOCUMENTS, PRODUKT_STATUS_AKTIV, produkte.STANDARD_PLAN, jetzt),
        )


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

    Unterstützt aktuell AUSSCHLIESSLICH das Backend `sqlite` - alle
    Schema-/Migrationsschritte hier (`PRAGMA`, `ALTER TABLE ... RENAME
    TO`, `INSERT OR IGNORE`, ...) sind SQLite-spezifisch (siehe
    `db_backend.py`s Moduldocstring für den genauen Umfang der
    DB-Abstraktion in diesem Block). `CLEVORIQ_DATABASE_BACKEND=postgresql`
    scheitert deshalb hier bewusst früh und klar, statt mit einer
    SQL-Syntaxfehlermeldung mitten in der Migration.
    """
    if db_backend.aktuelles_backend() != db_backend.BACKEND_SQLITE:
        raise NotImplementedError(
            "PostgreSQL wird als Datenbank-Backend vorbereitet (Verbindungsaufbau, "
            "Konfiguration über CLEVORIQ_DATABASE_URL - siehe db_backend.py), aber "
            "das SQL-Schema und alle Abfragen in speicher.py sind aktuell noch "
            "SQLite-spezifisch. Die vollständige Portierung ist für den nächsten "
            "Architekturblock vorgesehen. Setze CLEVORIQ_DATABASE_BACKEND=sqlite "
            "(Standard), um Clevoriq weiter zu betreiben."
        )

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
                storage_key TEXT,
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

            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES benutzer(id) ON DELETE CASCADE,
                token_hash TEXT NOT NULL UNIQUE,
                erstellt_am TEXT NOT NULL,
                last_activity_at TEXT NOT NULL,
                laeuft_ab_am TEXT NOT NULL,
                revoked_am TEXT
            );

            CREATE TABLE IF NOT EXISTS security_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                event_type TEXT NOT NULL,
                user_id INTEGER REFERENCES benutzer(id) ON DELETE SET NULL,
                identitaet TEXT,
                ip TEXT,
                erfolgreich INTEGER NOT NULL DEFAULT 1,
                detail TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token_hash);
            CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
            CREATE INDEX IF NOT EXISTS idx_security_events_lookup
                ON security_events(event_type, identitaet, ts);

            CREATE TABLE IF NOT EXISTS zwei_faktor (
                user_id INTEGER PRIMARY KEY REFERENCES benutzer(id) ON DELETE CASCADE,
                aktiv INTEGER NOT NULL DEFAULT 0,
                secret_verschluesselt TEXT,
                secret_key_version INTEGER,
                pending_secret_verschluesselt TEXT,
                pending_key_version INTEGER,
                pending_erstellt_am TEXT,
                letzter_zeitschritt INTEGER,
                aktiviert_am TEXT,
                aktualisiert_am TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS backup_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES benutzer(id) ON DELETE CASCADE,
                code_hash TEXT NOT NULL,
                erstellt_am TEXT NOT NULL,
                verwendet_am TEXT
            );

            CREATE TABLE IF NOT EXISTS zwei_faktor_challenges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES benutzer(id) ON DELETE CASCADE,
                challenge_token_hash TEXT NOT NULL UNIQUE,
                erstellt_am TEXT NOT NULL,
                laeuft_ab_am TEXT NOT NULL,
                fehlversuche INTEGER NOT NULL DEFAULT 0,
                verwendet_am TEXT,
                abgebrochen_am TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_backup_codes_user ON backup_codes(user_id);
            CREATE INDEX IF NOT EXISTS idx_2fa_challenges_token
                ON zwei_faktor_challenges(challenge_token_hash);
            CREATE INDEX IF NOT EXISTS idx_2fa_challenges_user ON zwei_faktor_challenges(user_id);

            CREATE TABLE IF NOT EXISTS produkt_zugriffe (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES benutzer(id) ON DELETE CASCADE,
                product_key TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'aktiv',
                plan TEXT NOT NULL DEFAULT 'standard',
                aktiviert_am TEXT NOT NULL,
                laeuft_ab_am TEXT,
                UNIQUE(user_id, product_key)
            );

            CREATE INDEX IF NOT EXISTS idx_produkt_zugriffe_user ON produkt_zugriffe(user_id);
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
                ("public_id", "TEXT"),
                ("storage_key", "TEXT"),
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
        _dokumente_public_ids_ergaenzen(conn)
        _dokumente_storage_keys_ergaenzen(conn)
        _produktzugriffe_migrieren(conn)
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

    Setzt `email_verified` EXPLIZIT auf 0: jede neue Registrierung ist
    zunächst unbestätigt und muss ihre E-Mail-Adresse über den
    zugesendeten Verifizierungslink bestätigen (siehe
    `email_verifizierung_erstellen`/`email_verifizierung_bestaetigen`
    und die Zugriffsbeschränkung in `web_app.py`). Das Tabellen-Default
    für `email_verified` bleibt bewusst bei 1 (siehe `datenbank_initialisieren`)
    - es gilt nur für Zeilen, die NICHT über diese Funktion angelegt
    wurden (das Migrations-/Bootstrap-Konto `_migrations_benutzer_id`
    und jede bereits vor dieser Funktions-Änderung bestehende Zeile),
    damit bestehende bzw. Entwicklungs-/Migrationskonten durch die neue
    Verifizierungspflicht nicht nachträglich ausgesperrt werden.

    Wirft `sqlite3.IntegrityError`, wenn Benutzername oder E-Mail schon
    vergeben sind (`UNIQUE`-Constraints) - `benutzer.py` prüft dies
    vorab bereits gezielt (für konkrete deutsche Fehlermeldungen), diese
    Funktion selbst verlässt sich aber nicht allein darauf, sondern auf
    die Datenbank-Constraints als letzte, verbindliche Schutzschicht.
    """
    jetzt = _jetzt()

    with _verbindung() as conn:
        cursor = conn.execute(
            "INSERT INTO benutzer "
            "(benutzername, email, passwort_hash, erstellt_am, aktualisiert_am, aktiv, email_verified) "
            "VALUES (?, ?, ?, ?, ?, 1, 0)",
            (benutzername.strip(), email.strip().lower(), auth.passwort_hash(passwort), jetzt, jetzt),
        )
        neue_id = cursor.lastrowid

        # Jeder neue Benutzer bekommt in dieser Entwicklungsphase
        # automatisch Zugriff auf Clevoriq Documents (siehe CLAUDE.md
        # "Produktsystem") - noch keine Kaufabwicklung, aber bereits über
        # dasselbe datengetriebene Zugriffsmodell wie jedes künftige
        # Produkt, nicht über eine Sonderbehandlung im Code.
        conn.execute(
            "INSERT INTO produkt_zugriffe "
            "(user_id, product_key, status, plan, aktiviert_am) "
            "VALUES (?, ?, ?, ?, ?)",
            (neue_id, produkte.PRODUKT_DOCUMENTS, PRODUKT_STATUS_AKTIV, produkte.STANDARD_PLAN, jetzt),
        )

        return neue_id


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

    Gibt `(erfolg: bool, meldung: str, user_id_oder_None)` zurück. Setzt
    `email_verified` nur, wenn die im Token hinterlegte E-Mail noch mit
    der AKTUELLEN E-Mail des Kontos übereinstimmt - verhindert, dass ein
    alter Link zu einer inzwischen erneut geänderten Adresse noch etwas
    bewirkt. `user_id` wird bei Erfolg zurückgegeben, damit der Aufrufer
    (siehe `web_app.py`) bei Bedarf eine PASSENDE, bereits angemeldete
    Sitzung aktualisieren kann - ohne dass dafür die aktuell angemeldete
    Sitzung blind als Ziel angenommen werden müsste (siehe Anforderung
    "bestehende Session darf nicht versehentlich einem anderen Benutzer
    zugeordnet werden").
    """
    with _verbindung() as conn:
        zeile = conn.execute(
            "SELECT id, user_id, email, laeuft_ab_am FROM email_verifications "
            "WHERE token_hash = ? AND verwendet_am IS NULL",
            (_token_hash(roher_token),),
        ).fetchone()

        if not zeile:
            return False, "Dieser Bestätigungslink ist ungültig oder wurde bereits verwendet.", None

        if datetime.fromisoformat(zeile["laeuft_ab_am"]) < datetime.now():
            return False, "Dieser Bestätigungslink ist abgelaufen.", None

        conn.execute(
            "UPDATE email_verifications SET verwendet_am = ? WHERE id = ?",
            (_jetzt(), zeile["id"]),
        )
        conn.execute(
            "UPDATE benutzer SET email_verified = 1 WHERE id = ? AND email = ?",
            (zeile["user_id"], zeile["email"]),
        )

    return True, "Deine E-Mail-Adresse wurde bestätigt.", zeile["user_id"]


def passwort_reset_anfordern(email_oder_benutzername):
    """Erzeugt (falls ein aktives Konto zu dieser Login-Kennung existiert)
    einen neuen Passwort-Reset-Token und gibt `(roher_token, konto_email)`
    im KLARTEXT zurück - gespeichert wird ausschließlich der Token-Hash.
    Ältere, noch nicht eingelöste Reset-Tokens desselben Benutzers werden
    zuvor ungültig gemacht. `konto_email` wird mit zurückgegeben, damit
    der Aufrufer (siehe `benutzer.py`) die Reset-Mail an die TATSÄCHLICHE
    Konto-Adresse schicken kann, auch wenn `email_oder_benutzername` ein
    Benutzername war - ohne dafür einen zweiten, separaten Lookup zu
    brauchen (der ein winziges Timing-Unterscheidungsmerkmal zwischen
    "Konto existiert"/"existiert nicht" wäre).

    Gibt `(None, None)` zurück, wenn keine passende Login-Kennung
    existiert - bewusst OHNE unterscheidbare Fehlermeldung an den
    Aufrufer, damit sich daraus nicht ableiten lässt, ob ein Konto zu
    dieser E-Mail/diesem Benutzernamen existiert (Schutz vor
    User-Enumeration).
    """
    benutzer = benutzer_nach_login(email_oder_benutzername)

    if not benutzer:
        return None, None

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

    return roher_token, benutzer["email"]


def passwort_reset_einloesen(roher_token, neues_passwort):
    """Löst einen Passwort-Reset-Token ein und setzt das neue Passwort.

    Gibt `(erfolg: bool, meldung: str, user_id_oder_None)` zurück.
    Einmalig verwendbar (`verwendet_am` wird sofort gesetzt) und
    zeitlich begrenzt gültig. Macht bei Erfolg zusätzlich ALLE
    Sitzungen dieses Benutzers ungültig (`sitzungen_widerrufen_fuer_benutzer`,
    ohne Ausnahme) - dieser Ablauf ist per Definition nicht an eine
    aktuell angemeldete Sitzung gebunden (das Zurücksetzen erfolgt über
    einen per E-Mail zugestellten Link, nicht im angemeldeten Zustand),
    ein Benutzer muss sich danach überall erneut mit dem neuen Passwort
    anmelden.
    """
    if not auth.passwort_stark_genug(neues_passwort):
        return False, (
            f"Das neue Passwort muss mindestens "
            f"{auth.MINDEST_PASSWORT_LAENGE} Zeichen enthalten."
        ), None

    with _verbindung() as conn:
        zeile = conn.execute(
            "SELECT id, user_id, laeuft_ab_am FROM password_resets "
            "WHERE token_hash = ? AND verwendet_am IS NULL",
            (_token_hash(roher_token),),
        ).fetchone()

        if not zeile:
            return False, "Dieser Link ist ungültig oder wurde bereits verwendet.", None

        if datetime.fromisoformat(zeile["laeuft_ab_am"]) < datetime.now():
            return False, "Dieser Link ist abgelaufen.", None

        conn.execute(
            "UPDATE benutzer SET passwort_hash = ?, aktualisiert_am = ? WHERE id = ?",
            (auth.passwort_hash(neues_passwort), _jetzt(), zeile["user_id"]),
        )
        conn.execute(
            "UPDATE password_resets SET verwendet_am = ? WHERE id = ?",
            (_jetzt(), zeile["id"]),
        )
        benutzer_id = zeile["user_id"]

    sitzungen_widerrufen_fuer_benutzer(benutzer_id)

    return True, "Dein Passwort wurde zurückgesetzt.", benutzer_id


# --- Serverseitige Sitzungen ---
#
# Ergänzt das bisherige, rein `st.session_state`-basierte Sitzungsmodell
# (siehe `benutzer.py`-Moduldocstring) um eine serverseitige, in der DB
# gespeicherte Gegenstelle: `st.session_state` selbst bleibt weiterhin
# NICHT netzwerk-/client-seitig manipulierbar (es ist kein Cookie), aber
# ohne einen serverseitigen Datensatz gäbe es keine Möglichkeit, eine
# Sitzung AKTIV zu widerrufen (Logout in einem anderen Tab, Passwort-
# Änderung/-Reset, Kontolöschung) - eine zweite gleichzeitig offene
# Streamlit-Sitzung desselben Benutzers (anderer Tab/anderes Gerät)
# würde sonst von einer solchen Aktion nichts mitbekommen. Gespeichert
# wird - wie bei den Verifizierungs-/Reset-Tokens - ausschließlich ein
# Hash des Sitzungs-Tokens, nie der Token selbst.
#
# Bekannte, im Streamlit-Modell verbleibende Grenze (siehe auch CLAUDE.md):
# es gibt kein HttpOnly/Secure-Cookie und keine Serverseitige Bindung an
# den Browser - der rohe Token liegt clientseitig nur in `st.session_state`
# des jeweiligen Tabs (nicht in der URL, nicht in einem für JavaScript
# lesbaren Cookie). Ein vollwertiger Schutz gegen Session-Hijacking auf
# Netzwerk-/Browser-Ebene erfordert eine echte Cookie-/Auth-Header-
# Architektur, die Streamlit in dieser Form nicht bietet.


def sitzung_erstellen(benutzer_id):
    """Erstellt eine neue serverseitige Sitzung und gibt ihren Klartext-
    Token zurück (einzige Gelegenheit - gespeichert wird nur sein Hash).

    IMMER ein neuer, frischer Token - nie eine Wiederverwendung eines
    bestehenden - schützt inhärent vor Session-Fixation: nach jedem
    erfolgreichen Login (`benutzer._login_versuchen`) bzw. jeder
    Registrierung entsteht eine komplett neue Sitzungs-Identität.
    """
    roher_token = secrets.token_urlsafe(32)
    jetzt = _jetzt()
    laeuft_ab = (
        datetime.now() + timedelta(hours=SITZUNG_MAX_LEBENSDAUER_STUNDEN)
    ).isoformat(timespec="seconds")

    with _verbindung() as conn:
        conn.execute(
            "INSERT INTO sessions (user_id, token_hash, erstellt_am, last_activity_at, laeuft_ab_am) "
            "VALUES (?, ?, ?, ?, ?)",
            (benutzer_id, _token_hash(roher_token), jetzt, jetzt, laeuft_ab),
        )

    return roher_token


def sitzung_pruefen_und_aktualisieren(roher_token):
    """Prüft eine Sitzung gegen die DB (widerrufen? abgelaufen? inaktiv?)
    und erneuert bei Gültigkeit `last_activity_at` (gleitendes
    Inaktivitäts-Fenster). Gibt die `user_id` bei Gültigkeit zurück,
    sonst `None` - MUSS bei jedem Lauf aufgerufen werden, solange eine
    Sitzung als "angemeldet" gilt (siehe `benutzer.sitzung_gueltig_pruefen`).
    """
    if not roher_token:
        return None

    with _verbindung() as conn:
        zeile = conn.execute(
            "SELECT id, user_id, last_activity_at, laeuft_ab_am FROM sessions "
            "WHERE token_hash = ? AND revoked_am IS NULL",
            (_token_hash(roher_token),),
        ).fetchone()

        if not zeile:
            return None

        jetzt_dt = datetime.now()

        if datetime.fromisoformat(zeile["laeuft_ab_am"]) < jetzt_dt:
            return None

        inaktiv_seit = datetime.fromisoformat(zeile["last_activity_at"])
        if inaktiv_seit + timedelta(minutes=SITZUNG_INAKTIVITAET_MINUTEN) < jetzt_dt:
            return None

        conn.execute(
            "UPDATE sessions SET last_activity_at = ? WHERE id = ?", (_jetzt(), zeile["id"])
        )

    return zeile["user_id"]


def sitzung_widerrufen(roher_token):
    """Beendet genau eine Sitzung (z. B. expliziter Logout)."""
    if not roher_token:
        return

    with _verbindung() as conn:
        conn.execute(
            "UPDATE sessions SET revoked_am = ? WHERE token_hash = ? AND revoked_am IS NULL",
            (_jetzt(), _token_hash(roher_token)),
        )


def sitzungen_widerrufen_fuer_benutzer(benutzer_id, ausser_roher_token=None):
    """Beendet ALLE Sitzungen dieses Benutzers, optional außer einer
    einzigen (`ausser_roher_token`, z. B. die gerade aktive Sitzung bei
    einer selbst durchgeführten Passwort-Änderung - siehe `konto.py`).
    Ohne Ausnahme (Standardfall, z. B. Passwort-Reset, Kontolöschung
    - dort per Fremdschlüssel-Kaskade ohnehin implizit) werden
    ausnahmslos alle Sitzungen beendet.
    """
    ausnahme_hash = _token_hash(ausser_roher_token) if ausser_roher_token else None

    with _verbindung() as conn:
        conn.execute(
            "UPDATE sessions SET revoked_am = ? "
            "WHERE user_id = ? AND revoked_am IS NULL AND token_hash IS NOT ?",
            (_jetzt(), benutzer_id, ausnahme_hash),
        )


# --- Security-/Audit-Ereignisse & persistentes Rate-Limiting ---
#
# Eine einzige Tabelle (`security_events`) bedient zwei Zwecke: (1) ein
# minimales Audit-Log sicherheitsrelevanter Ereignisse (Login-Erfolg/
# -Fehlschlag, Passwort-/E-Mail-Änderungen, ...), (2) die Datengrundlage
# für das persistente Rate-Limiting in `ratenbegrenzung.py` (Zählung von
# Versuchen je Aktion/Identität in einem gleitenden Zeitfenster). Bewusst
# NIE Passwörter, Tokens oder API-Keys - siehe `sicherheitslog.py`.


def sicherheitsereignis_speichern(event_type, user_id, identitaet, ip, erfolgreich, detail):
    with _verbindung() as conn:
        conn.execute(
            "INSERT INTO security_events "
            "(ts, event_type, user_id, identitaet, ip, erfolgreich, detail) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_jetzt(), event_type, user_id, identitaet, ip, 1 if erfolgreich else 0, detail),
        )


def sicherheitsereignisse_zaehlen(event_type, identitaet, seit_iso, nur_fehlgeschlagen=False):
    """Zählt Ereignisse eines Typs für eine (normalisierte) Identität seit
    einem Zeitpunkt - Grundlage für `ratenbegrenzung.pruefen`."""
    query = (
        "SELECT COUNT(*) AS anzahl FROM security_events "
        "WHERE event_type = ? AND identitaet = ? AND ts >= ?"
    )
    parameter = [event_type, identitaet, seit_iso]

    if nur_fehlgeschlagen:
        query += " AND erfolgreich = 0"

    with _verbindung() as conn:
        zeile = conn.execute(query, parameter).fetchone()

    return zeile["anzahl"]


def letztes_ereignis_zeitpunkt(event_type, identitaet):
    """Zeitstempel (ISO-String) des letzten Ereignisses dieses Typs für
    diese Identität, oder `None` - genutzt für Cooldowns/Eskalation."""
    with _verbindung() as conn:
        zeile = conn.execute(
            "SELECT ts FROM security_events WHERE event_type = ? AND identitaet = ? "
            "ORDER BY ts DESC LIMIT 1",
            (event_type, identitaet),
        ).fetchone()

    return zeile["ts"] if zeile else None


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
    (unvollständigen) Objekt-Bereinigung - das Konto selbst ist in
    jedem Fall bereits gelöscht (kein Login mehr möglich, keine
    hängenden Fremdschlüssel in der Datenbank).
    """
    with _verbindung() as conn:
        conn.execute("DELETE FROM benutzer WHERE id = ?", (benutzer_id,))

    try:
        # Löscht ALLE Storage-Objekte dieses Benutzers über den
        # Storage-Layer (siehe `storage.py`) statt direkt `shutil.rmtree`
        # aufzurufen - funktioniert unverändert für `LocalFileStorage`
        # UND (vorbereitet, noch nicht produktiv genutzt) für ein
        # künftiges `S3Storage`-Backend.
        _storage().praefix_loeschen(f"users/{int(benutzer_id)}/")
    except storage.StorageFehler as fehler:
        return str(fehler)

    return None


def dokument_datei_lesen(dokument_id, benutzer_id):
    """Liest die Original-Datei eines Dokuments als Bytes, NUR wenn es
    tatsächlich diesem Benutzer gehört (sonst `None`) - genutzt vom
    Datenexport (`datenexport.py`), damit dessen ZIP-Aufbau nie selbst
    einen Storage-Zugriff aus einer möglicherweise fremden `dokument_id`
    konstruieren muss, sondern die Eigentümerprüfung immer über diese
    Funktion läuft. Liest über den zentralen Storage-Layer (`storage.py`)
    statt einen lokalen Dateipfad vorauszusetzen.
    """
    with _verbindung() as conn:
        zeile = conn.execute(
            "SELECT storage_key FROM dokumente WHERE id = ? AND user_id = ?",
            (dokument_id, benutzer_id),
        ).fetchone()

    if not zeile or not zeile["storage_key"]:
        return None

    try:
        return _storage().lesen(zeile["storage_key"])
    except storage.StorageFehler:
        return None


# --- Dokumentbibliothek ---


def _storage():
    """Liefert den aktuell konfigurierten Storage-Layer (siehe `storage.py`)
    - FRISCH bei jedem Aufruf statt einmalig gecacht, damit Tests, die
    `APP_DATEN_ORDNER` auf ein temporäres Verzeichnis umleiten (siehe
    `test_hub_produkte.py`s `_TempDbTestCase`), automatisch auch den
    Storage-Layer mit umleiten. `APP_DATEN_ORDNER` ist nur für Backend
    `local` relevant (siehe `storage.storage_backend`)."""
    return storage.storage_backend(APP_DATEN_ORDNER)


def _benutzer_dokumente_ordner(benutzer_id):
    """Eigener Ablageordner je Benutzer für Original-Dateikopien.

    `benutzer_id` ist immer eine interne Ganzzahl aus der eigenen
    Datenbank (nie ein von außen übergebener String) - der Pfad ist
    dadurch inhärent sicher vor Path-Traversal, ganz ohne zusätzliche
    Sanitisierung des Wertes selbst. Wird nur noch von der
    (ausschließlich lokalen, dateisystembasierten) Alt-Migration
    `_dateien_migrieren` verwendet - der eigentliche Lese-/Schreib-/
    Löschpfad für Dokumente läuft über `_storage()`/`storage.py`.
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

    Der `storage_key` (siehe CLAUDE.md "Storage-Key statt öffentlicher
    Dateipfad") wird ausschließlich serverseitig aus `benutzer_id` +
    der neu erzeugten `public_id` gebildet - NIE aus dem (nutzergesteuerten)
    Original-Dateinamen `dateiname`, der ausschließlich als Anzeige-Metadatum
    in der DB landet.

    Upload-Konsistenz (siehe CLAUDE.md "Upload-Konsistenz"): die
    Storage-Datei wird ZUERST geschrieben, danach erst die DB-Zeile
    angelegt. Schlägt der Storage-Schreibvorgang fehl, existiert gar
    keine DB-Zeile (keine "Zeile ohne Datei"). Schlägt umgekehrt der
    DB-Insert fehl (z. B. eine unerwartete Ausnahme), wird das bereits
    geschriebene Storage-Objekt wieder gelöscht (Kompensation), bevor
    die Ausnahme weitergereicht wird - keine dauerhaft verwaiste Datei.
    """
    dokument_public_id = str(uuid.uuid4())
    storage_key = f"users/{benutzer_id}/documents/{dokument_public_id}/original.{dateityp}"
    storage_backend = _storage()

    storage_backend.speichern(storage_key, datei_bytes)

    try:
        with _verbindung() as conn:
            cursor = conn.execute(
                "INSERT INTO dokumente "
                "(user_id, dateiname, hash, seitenzahl, hochgeladen_am, dateityp, einheit_typ, "
                "groesse_bytes, public_id, storage_key) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    benutzer_id,
                    dateiname,
                    hash_wert,
                    einheiten_anzahl,
                    _jetzt(),
                    dateityp,
                    einheit_typ,
                    len(datei_bytes),
                    dokument_public_id,
                    storage_key,
                ),
            )
            dokument_id = cursor.lastrowid
    except Exception:
        try:
            storage_backend.loeschen(storage_key)
        except storage.StorageFehler:
            # Bestmögliche Kompensation - ein sekundärer Storage-Fehler
            # beim Aufräumen darf die eigentliche (aussagekräftigere)
            # DB-Ausnahme nicht verdecken.
            pass
        raise

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
            "SELECT storage_key FROM dokumente WHERE id = ? AND user_id = ?",
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

    if zeile["storage_key"]:
        try:
            _storage().loeschen(zeile["storage_key"])
        except storage.StorageFehler:
            # Die DB-Zeile (die Quelle der Wahrheit für den Benutzer) ist
            # bereits weg - ein Storage-Fehler hier darf das Löschen aus
            # der Bibliothek nicht rückgängig machen. Im schlimmsten Fall
            # bleibt ein für den Benutzer nicht mehr sichtbares, verwaistes
            # Objekt zurück (siehe CLAUDE.md "bekannte Einschränkungen").
            pass


def dokument_nach_public_id(public_id, benutzer_id):
    """Lädt ein Dokument über seine stabile, produktübergreifende
    `public_id` (siehe CLAUDE.md "Zentrale Dokument-ID") - NUR wenn es
    tatsächlich diesem Benutzer gehört, sonst `None`.

    Wie jede andere Dokument-Zugriffsfunktion dieser Datei ist die
    Eigentümerschaftsprüfung (`user_id = ?`) hier zwingend Teil der
    Abfrage selbst: die Kenntnis einer gültigen `public_id` (z. B. eines
    ANDEREN Benutzers) gewährt für sich genommen NIEMALS Zugriff - genau
    wie die interne, numerische `id` ist auch die `public_id` kein
    Zugriffs-Geheimnis, nur ein Bezeichner.
    """
    with _verbindung() as conn:
        zeile = conn.execute(
            "SELECT * FROM dokumente WHERE public_id = ? AND user_id = ?",
            (public_id, benutzer_id),
        ).fetchone()
        return dict(zeile) if zeile else None


def dokument_umbenennen(dokument_id, benutzer_id, neuer_dateiname):
    """Benennt ein Dokument DIESES Benutzers um (Anzeigename, nicht die
    Datei auf der Festplatte - die bleibt unter ihrem Hash-Namen
    unverändert, siehe `_benutzer_dokumente_ordner`).

    Wie `dokument_loeschen`: gehört die ID keinem Dokument dieses
    Benutzers, passiert schlicht nichts (kein Fehler, keine Änderung).
    Gibt `True` bei tatsächlicher Umbenennung zurück, sonst `False`
    (leerer Name oder kein passendes/eigenes Dokument).
    """
    neuer_dateiname = (neuer_dateiname or "").strip()

    if not neuer_dateiname:
        return False

    with _verbindung() as conn:
        cursor = conn.execute(
            "UPDATE dokumente SET dateiname = ? WHERE id = ? AND user_id = ?",
            (neuer_dateiname, dokument_id, benutzer_id),
        )
        return cursor.rowcount > 0


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


# --- Produktzugriffe ---
#
# Datengetriebenes Zugriffsmodell für die Clevoriq-Plattform (siehe
# CLAUDE.md "Produktsystem"/"Clevoriq Hub"): welche Produkte ein
# Benutzer nutzen darf, steht ausschließlich in `produkt_zugriffe`, nie
# in einer Code-Sonderbehandlung für ein bestimmtes Produkt. Aktuell
# gibt es real nur `produkte.PRODUKT_DOCUMENTS`; ein künftiges zweites
# Produkt braucht hier keine neue Funktion, nur weitere Zeilen mit einem
# neuen `product_key`.


def produkt_zugriff_gewaehren(benutzer_id, product_key, plan=None):
    """Gewährt (idempotent) aktiven Zugriff auf ein Produkt.

    Legt NUR an, wenn noch KEINE Zeile für dieses (Benutzer, Produkt)-Paar
    existiert (`UNIQUE(user_id, product_key)`) - ein bereits bestehender
    Eintrag (egal welchen Status er trägt, z. B. eine künftige manuelle
    Sperre) wird NIE stillschweigend überschrieben. Wird sowohl von
    `benutzer_erstellen` (neue Konten) als auch von der additiven
    Migration `_produktzugriffe_migrieren` (Alt-Konten) genutzt.
    """
    with _verbindung() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO produkt_zugriffe "
            "(user_id, product_key, status, plan, aktiviert_am) "
            "VALUES (?, ?, ?, ?, ?)",
            (benutzer_id, product_key, PRODUKT_STATUS_AKTIV, plan or produkte.STANDARD_PLAN, _jetzt()),
        )


def produkt_zugriff_aktiv(benutzer_id, product_key):
    """Zentrale, serverseitige Berechtigungsprüfung: darf dieser Benutzer
    dieses Produkt gerade nutzen?

    MUSS bei jedem Zugriff auf ein Produkt geprüft werden (siehe
    CLAUDE.md "deny by default") - nicht nur, um einen Button im Hub
    ein-/auszublenden, sondern auch bei jedem direkten Aufruf eines
    Produktbereichs (`web_app.py`), damit ein manipulierter/direkt
    gesetzter Bereichs-Zustand ohne gültige Berechtigung keinen Zugriff
    gewährt. Prüft sowohl `status = 'aktiv'` als auch ein optionales
    Ablaufdatum (`laeuft_ab_am`) - ein abgelaufener Zugriff zählt NICHT
    mehr als aktiv, selbst wenn die Zeile noch als "aktiv" markiert ist.
    """
    with _verbindung() as conn:
        zeile = conn.execute(
            "SELECT status, laeuft_ab_am FROM produkt_zugriffe "
            "WHERE user_id = ? AND product_key = ?",
            (benutzer_id, product_key),
        ).fetchone()

    if not zeile or zeile["status"] != PRODUKT_STATUS_AKTIV:
        return False

    if zeile["laeuft_ab_am"] and datetime.fromisoformat(zeile["laeuft_ab_am"]) < datetime.now():
        return False

    return True


def produkte_des_benutzers(benutzer_id):
    """Alle Produktzugriffszeilen DIESES Benutzers - Grundlage für die
    „Meine Produkte“-Ansicht im Clevoriq Hub (siehe `hub.py`)."""
    with _verbindung() as conn:
        zeilen = conn.execute(
            "SELECT product_key, status, plan, aktiviert_am, laeuft_ab_am "
            "FROM produkt_zugriffe WHERE user_id = ?",
            (benutzer_id,),
        ).fetchall()
        return [dict(zeile) for zeile in zeilen]


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


# --- Zwei-Faktor-Authentifizierung (TOTP) ---
#
# Persistenz für `zwei_faktor` (1:1 je Benutzer - Zeilenabwesenheit
# bedeutet "2FA nie eingerichtet/deaktiviert", das ist bewusst der
# Standardzustand JEDES bestehenden Kontos, ohne dass eine Migration
# nötig wäre), `backup_codes` (nur Hashes, nie Klartext) und
# `zwei_faktor_challenges` (die serverseitige Login-Challenge zwischen
# korrektem Passwort und vollständiger Anmeldung). Alle drei Tabellen
# kaskadieren über `ON DELETE CASCADE` von `benutzer(id)` - eine
# Kontolöschung (`konto_endgueltig_loeschen`) entfernt daher automatisch
# ALLE 2FA-Daten mit, ohne dass dieser Abschnitt dafür etwas Eigenes tun
# müsste. Die eigentliche TOTP-/Verschlüsselungs-/Backup-Code-Logik lebt
# in `zwei_faktor_krypto.py` (Streamlit-/DB-unabhängig) - dieser
# Abschnitt bindet sie nur an die Datenbank an, analog zur Trennung
# `auth.py`/`speicher.py` bei Passwörtern.


def _ist_abgelaufen(zeitstempel_iso, minuten):
    if not zeitstempel_iso:
        return True
    return datetime.fromisoformat(zeitstempel_iso) + timedelta(minutes=minuten) < datetime.now()


def zwei_faktor_status(benutzer_id):
    """Gibt `{"aktiv": bool, "pending": bool}` zurück. Ein PENDING-Secret,
    dessen Einrichtungsfrist (`PENDING_2FA_GUELTIGKEIT_MINUTEN`) verstrichen
    ist, wird hier lazy verworfen (siehe `_pending_verwerfen`) - ein
    abgebrochenes Setup blockiert dadurch nie einen späteren Neustart."""
    with _verbindung() as conn:
        zeile = conn.execute(
            "SELECT aktiv, pending_secret_verschluesselt, pending_erstellt_am "
            "FROM zwei_faktor WHERE user_id = ?",
            (benutzer_id,),
        ).fetchone()

    if not zeile:
        return {"aktiv": False, "pending": False}

    pending_vorhanden = bool(zeile["pending_secret_verschluesselt"])
    pending_abgelaufen = pending_vorhanden and _ist_abgelaufen(
        zeile["pending_erstellt_am"], PENDING_2FA_GUELTIGKEIT_MINUTEN
    )

    if pending_vorhanden and pending_abgelaufen:
        _pending_verwerfen(benutzer_id)

    return {"aktiv": bool(zeile["aktiv"]), "pending": pending_vorhanden and not pending_abgelaufen}


def _pending_verwerfen(benutzer_id):
    with _verbindung() as conn:
        conn.execute(
            "UPDATE zwei_faktor SET pending_secret_verschluesselt = NULL, "
            "pending_key_version = NULL, pending_erstellt_am = NULL, aktualisiert_am = ? "
            "WHERE user_id = ?",
            (_jetzt(), benutzer_id),
        )


def zwei_faktor_setup_starten(benutzer_id):
    """Erzeugt ein neues PENDING-TOTP-Secret (ersetzt ein evtl. noch
    vorhandenes altes Pending-Secret) und gibt `(klartext_secret,
    otpauth_uri)` zurück - das Secret im Klartext wird NIRGENDS
    gespeichert, nur seine verschlüsselte Form. Rührt ein eventuell
    bereits AKTIVES Secret nicht an: sowohl die Erst-Einrichtung als auch
    eine spätere Neu-Einrichtung/Rotation (siehe `konto.py`) ersetzen das
    aktive Secret erst bei erfolgreicher Bestätigung des neuen Codes
    (`zwei_faktor_setup_bestaetigen`), nie vorher.
    """
    email = benutzer_konto_daten(benutzer_id)["email"]
    klartext_secret = zwei_faktor_krypto.neues_totp_secret()
    chiffrat, key_version = zwei_faktor_krypto.secret_verschluesseln(klartext_secret)
    jetzt = _jetzt()

    with _verbindung() as conn:
        vorhanden = conn.execute(
            "SELECT 1 FROM zwei_faktor WHERE user_id = ?", (benutzer_id,)
        ).fetchone()

        if vorhanden:
            conn.execute(
                "UPDATE zwei_faktor SET pending_secret_verschluesselt = ?, "
                "pending_key_version = ?, pending_erstellt_am = ?, aktualisiert_am = ? "
                "WHERE user_id = ?",
                (chiffrat, key_version, jetzt, jetzt, benutzer_id),
            )
        else:
            conn.execute(
                "INSERT INTO zwei_faktor "
                "(user_id, aktiv, pending_secret_verschluesselt, pending_key_version, "
                "pending_erstellt_am, aktualisiert_am) VALUES (?, 0, ?, ?, ?, ?)",
                (benutzer_id, chiffrat, key_version, jetzt, jetzt),
            )

    return klartext_secret, zwei_faktor_krypto.otpauth_uri(klartext_secret, email)


def zwei_faktor_setup_abbrechen(benutzer_id):
    """Verwirft ein laufendes Pending-Setup vorzeitig (Nutzer bricht die
    Einrichtung selbst ab, siehe `konto.py`) - dasselbe Aufräumen, das
    sonst lazy nach `PENDING_2FA_GUELTIGKEIT_MINUTEN` passiert, nur
    sofort statt erst beim nächsten Statuscheck. Ein bereits AKTIVES
    Secret bleibt davon unberührt."""
    _pending_verwerfen(benutzer_id)


def zwei_faktor_setup_bestaetigen(benutzer_id, code):
    """Prüft `code` gegen das PENDING-Secret; bei Erfolg wird es zum
    AKTIVEN Secret (Erst-Einrichtung ODER Ersetzen eines vorhandenen
    aktiven Secrets bei einer Neu-Einrichtung/Rotation - siehe
    `konto.py`), und neue Backup-Codes werden erzeugt (siehe
    `zwei_faktor_backup_codes_neu_erzeugen`, macht dabei automatisch alte
    Backup-Codes ungültig). Gibt `(erfolg, meldung, backup_codes_klartext)`
    zurück - `backup_codes_klartext` ist NUR bei Erfolg gesetzt (einzige
    Gelegenheit, sie zu sehen).
    """
    with _verbindung() as conn:
        zeile = conn.execute(
            "SELECT pending_secret_verschluesselt, pending_key_version, pending_erstellt_am "
            "FROM zwei_faktor WHERE user_id = ?",
            (benutzer_id,),
        ).fetchone()

    if not zeile or not zeile["pending_secret_verschluesselt"]:
        return False, "Es läuft gerade keine 2FA-Einrichtung. Bitte starte sie erneut.", None

    if _ist_abgelaufen(zeile["pending_erstellt_am"], PENDING_2FA_GUELTIGKEIT_MINUTEN):
        _pending_verwerfen(benutzer_id)
        return False, "Die Einrichtung ist abgelaufen. Bitte starte sie erneut.", None

    try:
        secret = zwei_faktor_krypto.secret_entschluesseln(
            zeile["pending_secret_verschluesselt"], zeile["pending_key_version"]
        )
    except RuntimeError:
        return False, "2FA konnte technisch nicht geprüft werden. Bitte später erneut versuchen.", None

    gueltig, _zeitschritt = zwei_faktor_krypto.totp_code_pruefen(secret, code)

    if not gueltig:
        return False, "Der eingegebene Code ist falsch oder abgelaufen.", None

    jetzt = _jetzt()

    with _verbindung() as conn:
        conn.execute(
            "UPDATE zwei_faktor SET aktiv = 1, secret_verschluesselt = ?, "
            "secret_key_version = ?, letzter_zeitschritt = ?, "
            "pending_secret_verschluesselt = NULL, pending_key_version = NULL, "
            "pending_erstellt_am = NULL, aktiviert_am = ?, aktualisiert_am = ? "
            "WHERE user_id = ?",
            (
                zeile["pending_secret_verschluesselt"],
                zeile["pending_key_version"],
                _zeitschritt,
                jetzt,
                jetzt,
                benutzer_id,
            ),
        )

    backup_codes_klartext = zwei_faktor_backup_codes_neu_erzeugen(benutzer_id)

    return True, "Zwei-Faktor-Authentifizierung wurde aktiviert.", backup_codes_klartext


def zwei_faktor_backup_codes_neu_erzeugen(benutzer_id):
    """Erzeugt `BACKUP_CODES_ANZAHL` neue Backup-Codes und macht dabei
    ALLE bisherigen Codes dieses Benutzers sofort ungültig (harte
    Ersetzung, kein Anhängen). Gibt die neuen Codes im KLARTEXT zurück -
    einzige Gelegenheit, sie zu sehen; gespeichert werden ausschließlich
    ihre Hashes (`auth.backup_code_hash` - gesalzenes Argon2id, dieselbe
    Konfiguration wie für Passwörter, da ein Backup-Code genau wie ein
    Passwort ein authentifizierungsrelevantes Geheimnis ist)."""
    klartext_codes = zwei_faktor_krypto.backup_codes_erzeugen(BACKUP_CODES_ANZAHL)
    jetzt = _jetzt()

    with _verbindung() as conn:
        conn.execute("DELETE FROM backup_codes WHERE user_id = ?", (benutzer_id,))
        conn.executemany(
            "INSERT INTO backup_codes (user_id, code_hash, erstellt_am) VALUES (?, ?, ?)",
            [
                (
                    benutzer_id,
                    auth.backup_code_hash(zwei_faktor_krypto.backup_code_normalisieren(code)),
                    jetzt,
                )
                for code in klartext_codes
            ],
        )

    return klartext_codes


def zwei_faktor_backup_codes_anzahl_uebrig(benutzer_id):
    """Anzahl noch UNVERWENDETER Backup-Codes - für eine reine
    Status-Anzeige ("noch 7 von 10 übrig"). Verrät nie, WELCHE Codes
    noch gültig sind."""
    with _verbindung() as conn:
        zeile = conn.execute(
            "SELECT COUNT(*) AS anzahl FROM backup_codes WHERE user_id = ? AND verwendet_am IS NULL",
            (benutzer_id,),
        ).fetchone()

    return zeile["anzahl"]


def zwei_faktor_backup_code_pruefen_und_verbrauchen(benutzer_id, code):
    """Prüft einen Backup-Code gegen die gespeicherten Argon2id-Hashes
    DIESES Benutzers und markiert ihn bei Erfolg SOFORT als verbraucht
    (einmalig verwendbar). Gibt `True`/`False` zurück.

    Da Argon2id-Hashes gesalzen sind (anders als der einfache SHA-256 für
    hochentropische Tokens), ist kein direkter `WHERE code_hash = ?`-
    Abgleich mehr möglich - stattdessen werden alle noch unverbrauchten
    Codes dieses Benutzers geladen und `code` wird gegen jeden einzeln
    mit `auth.backup_code_pruefen` verifiziert. Das ist bei maximal
    `BACKUP_CODES_ANZAHL` (10) Zeilen unproblematisch."""
    normalisiert = zwei_faktor_krypto.backup_code_normalisieren(code)

    if not normalisiert:
        return False

    with _verbindung() as conn:
        zeilen = conn.execute(
            "SELECT id, code_hash FROM backup_codes WHERE user_id = ? AND verwendet_am IS NULL",
            (benutzer_id,),
        ).fetchall()

        treffer_id = None
        for zeile in zeilen:
            if auth.backup_code_pruefen(normalisiert, zeile["code_hash"]):
                treffer_id = zeile["id"]
                break

        if treffer_id is None:
            return False

        conn.execute("UPDATE backup_codes SET verwendet_am = ? WHERE id = ?", (_jetzt(), treffer_id))

    return True


def zwei_faktor_totp_pruefen(benutzer_id, code):
    """Prüft `code` gegen das AKTIVE TOTP-Secret dieses Benutzers
    inklusive Replay-Schutz (verhindert, dass derselbe Zeitschritt
    zweimal akzeptiert wird - siehe `zwei_faktor_krypto.totp_code_pruefen`)
    und aktualisiert bei Erfolg `letzter_zeitschritt`. Gibt
    `(gueltig, technische_fehlermeldung_oder_None)` zurück - die
    Fehlermeldung ist NUR bei einem technischen Problem (z. B. fehlender/
    ungültiger Verschlüsselungsschlüssel) gesetzt, NIE bei einem einfach
    falschen Code (dafür zeigen Aufrufer ihre eigene, kontextpassende
    Meldung)."""
    with _verbindung() as conn:
        zeile = conn.execute(
            "SELECT secret_verschluesselt, secret_key_version, letzter_zeitschritt "
            "FROM zwei_faktor WHERE user_id = ? AND aktiv = 1",
            (benutzer_id,),
        ).fetchone()

    if not zeile:
        return False, "Zwei-Faktor-Authentifizierung ist für dieses Konto nicht aktiv."

    try:
        secret = zwei_faktor_krypto.secret_entschluesseln(
            zeile["secret_verschluesselt"], zeile["secret_key_version"]
        )
    except RuntimeError:
        return False, "2FA konnte technisch nicht geprüft werden. Bitte später erneut versuchen."

    gueltig, zeitschritt = zwei_faktor_krypto.totp_code_pruefen(
        secret, code, letzter_zeitschritt=zeile["letzter_zeitschritt"]
    )

    if gueltig:
        with _verbindung() as conn:
            conn.execute(
                "UPDATE zwei_faktor SET letzter_zeitschritt = ? WHERE user_id = ?",
                (zeitschritt, benutzer_id),
            )

    return gueltig, None


def zwei_faktor_code_pruefen(benutzer_id, code, ist_backup_code):
    """Vereinheitlichter Einstiegspunkt: prüft entweder einen TOTP- oder
    einen Backup-Code gegen DIESEN Benutzer - genutzt sowohl von der
    Login-Challenge (`zwei_faktor_challenge_pruefen_und_verbrauchen`) als
    auch von jeder Re-Authentifizierung bei sicherheitskritischen
    Kontoänderungen (2FA deaktivieren, Backup-Codes neu erzeugen, 2FA neu
    einrichten, E-Mail ändern, Konto löschen - siehe `konto.py`). Gibt
    `(gueltig, technische_fehlermeldung_oder_None)` zurück."""
    if ist_backup_code:
        return zwei_faktor_backup_code_pruefen_und_verbrauchen(benutzer_id, code), None

    return zwei_faktor_totp_pruefen(benutzer_id, code)


def zwei_faktor_deaktivieren(benutzer_id):
    """Deaktiviert 2FA vollständig: entfernt das verschlüsselte Secret
    (aktiv UND pending), setzt `aktiv = 0` und löscht ALLE Backup-Codes
    dieses Benutzers. Ruft KEINE Sitzungs-Invalidierung auf - das
    entscheidet die aufrufende UI (`konto.py`), die zusätzlich weiß,
    welche Sitzung dabei ausgenommen werden soll."""
    with _verbindung() as conn:
        conn.execute(
            "UPDATE zwei_faktor SET aktiv = 0, secret_verschluesselt = NULL, "
            "secret_key_version = NULL, letzter_zeitschritt = NULL, "
            "pending_secret_verschluesselt = NULL, pending_key_version = NULL, "
            "pending_erstellt_am = NULL, aktiviert_am = NULL, aktualisiert_am = ? "
            "WHERE user_id = ?",
            (_jetzt(), benutzer_id),
        )
        conn.execute("DELETE FROM backup_codes WHERE user_id = ?", (benutzer_id,))


def zwei_faktor_challenge_erstellen(benutzer_id):
    """Erstellt eine neue serverseitige 2FA-Login-Challenge (NACH
    erfolgreicher Passwortprüfung, VOR vollständiger Anmeldung) und gibt
    ihren Klartext-Token zurück (gespeichert wird nur sein Hash, analog
    zu Sitzungs-/Verifizierungs-Tokens). Zeitlich begrenzt gültig
    (`ZWEI_FAKTOR_CHALLENGE_GUELTIGKEIT_MINUTEN`) und - über
    `zwei_faktor_challenge_pruefen_und_verbrauchen` - einmalig verwendbar
    sowie nach `ZWEI_FAKTOR_CHALLENGE_MAX_FEHLVERSUCHE` Fehlversuchen
    endgültig ungültig."""
    roher_token = secrets.token_urlsafe(32)
    jetzt = _jetzt()
    laeuft_ab = (
        datetime.now() + timedelta(minutes=ZWEI_FAKTOR_CHALLENGE_GUELTIGKEIT_MINUTEN)
    ).isoformat(timespec="seconds")

    with _verbindung() as conn:
        conn.execute(
            "INSERT INTO zwei_faktor_challenges "
            "(user_id, challenge_token_hash, erstellt_am, laeuft_ab_am) VALUES (?, ?, ?, ?)",
            (benutzer_id, _token_hash(roher_token), jetzt, laeuft_ab),
        )

    return roher_token


def zwei_faktor_challenge_pruefen_und_verbrauchen(roher_token, code, ist_backup_code):
    """Prüft + verbraucht eine 2FA-Login-Challenge serverseitig - der
    `benutzer_id`-Bezug kommt IMMER aus der Challenge-Zeile selbst (nie
    aus einem Aufrufer-Parameter), ein manipulierter/geratener Token
    findet dadurch bestenfalls gar keine Zeile, nie die eines fremden
    Benutzers.

    Gibt `(erfolg, meldung, benutzer_id_oder_None, challenge_beendet)`
    zurück. `challenge_beendet=True` bedeutet: die Challenge ist (durch
    Erfolg ODER zu viele Fehlversuche ODER Ablauf) nicht mehr benutzbar -
    der Aufrufer MUSS den gemerkten Challenge-Token verwerfen und zu
    einem normalen Login zurückkehren, ein erneuter Versuch mit
    demselben Token liefert danach immer `erfolg=False` (Challenge nicht
    gefunden), egal wie korrekt der Code ist.
    """
    token_hash = _token_hash(roher_token) if roher_token else None

    with _verbindung() as conn:
        zeile = conn.execute(
            "SELECT id, user_id, laeuft_ab_am, fehlversuche FROM zwei_faktor_challenges "
            "WHERE challenge_token_hash = ? AND verwendet_am IS NULL AND abgebrochen_am IS NULL",
            (token_hash,),
        ).fetchone()

    if not zeile:
        return False, "Diese Anmeldung ist nicht mehr gültig. Bitte melde dich erneut an.", None, True

    if datetime.fromisoformat(zeile["laeuft_ab_am"]) < datetime.now():
        with _verbindung() as conn:
            conn.execute(
                "UPDATE zwei_faktor_challenges SET abgebrochen_am = ? WHERE id = ?",
                (_jetzt(), zeile["id"]),
            )
        return (
            False,
            "Die Anmeldung ist abgelaufen. Bitte melde dich erneut an.",
            zeile["user_id"],
            True,
        )

    benutzer_id = zeile["user_id"]
    gueltig, _technische_meldung = zwei_faktor_code_pruefen(benutzer_id, code, ist_backup_code)

    if gueltig:
        with _verbindung() as conn:
            conn.execute(
                "UPDATE zwei_faktor_challenges SET verwendet_am = ? WHERE id = ?",
                (_jetzt(), zeile["id"]),
            )
        return True, None, benutzer_id, True

    neue_fehlversuche = zeile["fehlversuche"] + 1
    challenge_beendet = neue_fehlversuche >= ZWEI_FAKTOR_CHALLENGE_MAX_FEHLVERSUCHE

    with _verbindung() as conn:
        if challenge_beendet:
            conn.execute(
                "UPDATE zwei_faktor_challenges SET fehlversuche = ?, abgebrochen_am = ? WHERE id = ?",
                (neue_fehlversuche, _jetzt(), zeile["id"]),
            )
        else:
            conn.execute(
                "UPDATE zwei_faktor_challenges SET fehlversuche = ? WHERE id = ?",
                (neue_fehlversuche, zeile["id"]),
            )

    if challenge_beendet:
        return False, "Zu viele Fehlversuche. Bitte melde dich erneut an.", benutzer_id, True

    return False, "Der eingegebene Code ist falsch.", benutzer_id, False
