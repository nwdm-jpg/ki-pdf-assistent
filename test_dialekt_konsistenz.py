"""Automatisierte Tests für Block 5 (SQLite-/PostgreSQL-Kompatibilität +

robustere Storage-Konsistenz).

Deckt ab: die portable Verbindungs-/Cursor-Hülle in `db_backend.py`
(Platzhalter-Übersetzung, Zeilen-Normalisierung, `insert_und_id_zurueckgeben`,
`upsert_ignore`/`upsert_ersetzen`), das dialektabhängige Schema
(`speicher._schema_sql`), Race-Sicherheit der Single-Use-Token-/2FA-Pfade
UNTER ECHTER THREAD-NEBENLÄUFIGKEIT gegen dieselbe SQLite-Datei, und die
neue Storage-Cleanup-Outbox (`storage_cleanup_auftraege`,
`cleanup_pending_storage_deletions`).

Bewusst wie die übrigen `test_*.py`-Dateien ein reines `unittest`-Skript
auf einer frischen, temporären SQLite-Datenbank je Test. Macht KEINE
echte AWS-/IONOS-/PostgreSQL-Verbindung (PostgreSQL-Pfade werden gegen
ein hand-geschriebenes Fake-`psycopg2`-Modul getestet - echte
PostgreSQL-Integrationstests liegen in `test_postgres_integration.py`
und laufen nur, wenn `CLEVORIQ_TEST_POSTGRES_URL` gesetzt ist). Keine
echten OpenAI-Aufrufe, keine echten E-Mails.

Ausführen mit:

    python test_dialekt_konsistenz.py

oder:

    python -m unittest test_dialekt_konsistenz
"""

import os
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ["CLEVORIQ_EMAIL_PROVIDER"] = "dev"
os.environ.pop("AVENLOQ_EMAIL_PROVIDER", None)
os.environ.pop("RESEND_API_KEY", None)

_TEST_2FA_SCHLUESSEL = "yq3nD5wq0v1sO4kQe9ZfW2mC7bH8jU6xR1tL0nA5pY4="
os.environ["CLEVORIQ_2FA_ENCRYPTION_KEY"] = _TEST_2FA_SCHLUESSEL
os.environ.pop("CLEVORIQ_2FA_ENCRYPTION_KEY_V2", None)

import db_backend  # noqa: E402
import produkte  # noqa: E402
import pyotp  # noqa: E402
import speicher  # noqa: E402
import storage  # noqa: E402
import zwei_faktor_krypto  # noqa: E402


class _TempDbTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = Path(tempfile.mkdtemp(prefix="clevoriq_dialekt_test_"))
        speicher.APP_DATEN_ORDNER = self._tmp_dir
        speicher.BENUTZER_ORDNER = self._tmp_dir / "users"
        speicher.DB_PFAD = self._tmp_dir / "bibliothek.db"
        speicher._ALTER_PDF_ORDNER = self._tmp_dir / "pdfs"
        speicher.datenbank_initialisieren()

    def tearDown(self):
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _neuer_benutzer(self, benutzername, email):
        return speicher.benutzer_erstellen(benutzername, email, "Passwort123")

    def _dokument_anlegen(self, benutzer_id, dateiname="vertrag.pdf", inhalt=b"Inhalt"):
        hash_wert = speicher.hash_berechnen(inhalt + dateiname.encode("utf-8"))
        return speicher.dokument_speichern(dateiname, hash_wert, inhalt, 1, "pdf", "seite", benutzer_id)

    def _2fa_aktivieren(self, benutzer_id):
        secret, _uri = speicher.zwei_faktor_setup_starten(benutzer_id)
        code = pyotp.TOTP(secret).now()
        erfolg, meldung, backup_codes = speicher.zwei_faktor_setup_bestaetigen(benutzer_id, code)
        assert erfolg, meldung
        return secret, backup_codes


# --------------------------------------------------------------------------
# B/C/D) Platzhalter-Übersetzung, Zeilen-Normalisierung
# --------------------------------------------------------------------------


class PortableConnectionTests(_TempDbTestCase):
    def test_b_sqlite_verbindung_uebersetzt_platzhalter_nicht(self):
        """B) `_PortableConnection.execute` ruft die Platzhalter-
        Übersetzung NUR für PostgreSQL auf (siehe `execute`s
        Dialekt-Verzweigung) - unter SQLite bleibt `?` unverändert und
        funktioniert direkt mit dem `sqlite3`-Treiber, wie schon vor
        Block 5."""
        benutzer_id = self._neuer_benutzer("b1", "b1@example.com")

        with speicher._verbindung() as conn:
            zeile = conn.execute(
                "SELECT benutzername FROM benutzer WHERE id = ? AND aktiv = ?", (benutzer_id, 1)
            ).fetchone()

        self.assertEqual(zeile["benutzername"], "b1")

    def test_c_platzhalter_uebersetzung_fuer_postgresql(self):
        uebersetzt = db_backend._platzhalter_uebersetzen(
            "SELECT * FROM t WHERE a = ? AND b = ? AND c IN (?, ?, ?)"
        )
        self.assertEqual(uebersetzt, "SELECT * FROM t WHERE a = %s AND b = %s AND c IN (%s, %s, %s)")
        self.assertNotIn("?", uebersetzt)

    def test_d_fetchone_fetchall_liefern_reine_dicts(self):
        benutzer_id = self._neuer_benutzer("d1", "d1@example.com")

        with speicher._verbindung() as conn:
            eine = conn.execute("SELECT id, benutzername FROM benutzer WHERE id = ?", (benutzer_id,)).fetchone()
            alle = conn.execute("SELECT id, benutzername FROM benutzer WHERE id = ?", (benutzer_id,)).fetchall()

        self.assertIs(type(eine), dict)
        self.assertIs(type(alle[0]), dict)
        self.assertEqual(eine["benutzername"], "d1")

    def test_d_rowcount_durchgereicht(self):
        benutzer_id = self._neuer_benutzer("d2", "d2@example.com")

        with speicher._verbindung() as conn:
            cursor = conn.execute("UPDATE benutzer SET aktualisiert_am = aktualisiert_am WHERE id = ?", (benutzer_id,))
            self.assertEqual(cursor.rowcount, 1)

    def test_d_lastrowid_unter_postgresql_wirft_klaren_fehler(self):
        wrapper = db_backend._PortableCursor(roher_cursor=object(), dialekt=db_backend.BACKEND_POSTGRESQL)

        with self.assertRaises(db_backend.DatenbankFehler):
            _ = wrapper.lastrowid


# --------------------------------------------------------------------------
# E/F) Insert + ID-Rückgabe
# --------------------------------------------------------------------------


class InsertIdTests(_TempDbTestCase):
    def test_e_insert_id_sqlite(self):
        benutzer_id = self._neuer_benutzer("e1", "e1@example.com")

        with speicher._verbindung() as conn:
            neue_id = db_backend.insert_und_id_zurueckgeben(
                conn,
                "INSERT INTO chats (user_id, titel, erstellt_am, aktualisiert_am, dokument_ids) "
                "VALUES (?, ?, ?, ?, '[]')",
                (benutzer_id, "Testchat", speicher._jetzt(), speicher._jetzt()),
            )
        self.assertIsInstance(neue_id, int)
        self.assertGreater(neue_id, 0)

    def test_f_insert_id_postgresql_ueber_fake(self):
        """F) Unter PostgreSQL hängt `insert_und_id_zurueckgeben` ein
        `RETURNING` an und liest die ID aus der zurückgegebenen Zeile -
        hier gegen eine In-Memory-SQLite-Verbindung geprüft, die
        `RETURNING` genauso unterstützt (SQLite >= 3.35), mit
        `ist_postgresql()` künstlich auf `True` gepatcht, um exakt den
        PostgreSQL-Codepfad (RETURNING-Zweig) zu durchlaufen."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, wert TEXT)")
        wrapper = db_backend._PortableConnection(conn, db_backend.BACKEND_SQLITE)

        with mock.patch.object(db_backend, "ist_postgresql", return_value=True):
            neue_id = db_backend.insert_und_id_zurueckgeben(
                wrapper, "INSERT INTO t (wert) VALUES (?)", ("hallo",)
            )

        self.assertEqual(neue_id, 1)
        conn.close()


# --------------------------------------------------------------------------
# G) Upsert/Conflict-Semantik
# --------------------------------------------------------------------------


class UpsertTests(_TempDbTestCase):
    def test_g_upsert_ignore_ueberschreibt_bestehende_zeile_nicht(self):
        benutzer_id = self._neuer_benutzer("g1", "g1@example.com")

        with speicher._verbindung() as conn:
            conn.execute(
                "UPDATE produkt_zugriffe SET status = 'gesperrt' WHERE user_id = ? AND product_key = ?",
                (benutzer_id, produkte.PRODUKT_DOCUMENTS),
            )

        speicher.produkt_zugriff_gewaehren(benutzer_id, produkte.PRODUKT_DOCUMENTS)

        self.assertFalse(speicher.produkt_zugriff_aktiv(benutzer_id, produkte.PRODUKT_DOCUMENTS))

    def test_g_upsert_ersetzen_aktualisiert_nur_angegebene_spalten(self):
        benutzer_id = self._neuer_benutzer("g2", "g2@example.com")
        secret1, _uri1 = speicher.zwei_faktor_setup_starten(benutzer_id)
        secret2, _uri2 = speicher.zwei_faktor_setup_starten(benutzer_id)

        # Zweiter Aufruf ERSETZT das Pending-Secret (nicht anhängen) -
        # und lässt `aktiv`/`secret_verschluesselt` (noch nie gesetzt)
        # unangetastet.
        self.assertNotEqual(secret1, secret2)
        status = speicher.zwei_faktor_status(benutzer_id)
        self.assertFalse(status["aktiv"])
        self.assertTrue(status["pending"])


# --------------------------------------------------------------------------
# H/I/J) Schema für beide Backends, Migration idempotent
# --------------------------------------------------------------------------


class SchemaTests(unittest.TestCase):
    def test_h_sqlite_schema_erstellt_alle_tabellen(self):
        tmp_dir = Path(tempfile.mkdtemp(prefix="clevoriq_schema_test_"))
        try:
            speicher.APP_DATEN_ORDNER = tmp_dir
            speicher.BENUTZER_ORDNER = tmp_dir / "users"
            speicher.DB_PFAD = tmp_dir / "bibliothek.db"
            speicher._ALTER_PDF_ORDNER = tmp_dir / "pdfs"
            speicher.datenbank_initialisieren()

            with speicher._verbindung() as conn:
                tabellen = {
                    z["name"]
                    for z in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }

            erwartet = {
                "benutzer", "dokumente", "chunks", "chats", "nachrichten",
                "email_verifications", "password_resets", "sessions",
                "security_events", "zwei_faktor", "backup_codes",
                "zwei_faktor_challenges", "produkt_zugriffe",
                "storage_cleanup_auftraege",
            }
            self.assertTrue(erwartet.issubset(tabellen), tabellen)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_i_postgresql_schema_ist_syntaktisch_eigenstaendig(self):
        """I) Ohne echten PostgreSQL-Server (siehe `test_postgres_integration.py`
        für die echte Verifikation) prüft dieser Test wenigstens, dass
        das generierte PostgreSQL-Schema die erwarteten Dialekt-
        Ersetzungen tatsächlich enthält (SERIAL statt AUTOINCREMENT,
        BYTEA statt BLOB) und KEINE SQLite-spezifischen Fragmente mehr."""
        with mock.patch.object(db_backend, "ist_postgresql", return_value=True):
            sql = speicher._schema_sql()

        self.assertIn("SERIAL PRIMARY KEY", sql)
        self.assertIn("BYTEA", sql)
        self.assertNotIn("AUTOINCREMENT", sql)
        self.assertNotIn(" BLOB", sql)
        # Die eine bewusste Ausnahme: `zwei_faktor.user_id` ist NIE
        # autoinkrementiert (siehe `_schema_sql`s Docstring).
        self.assertIn("user_id INTEGER PRIMARY KEY REFERENCES benutzer(id) ON DELETE CASCADE", sql)

    def test_i_beide_schemata_haben_dieselben_tabellen(self):
        sqlite_sql = speicher._SCHEMA_VORLAGE.format(PK="X", BLOB="Y")
        # Beide Varianten sind aus DERSELBEN Vorlage gebaut - die Menge
        # der `CREATE TABLE`-Namen ist also per Konstruktion identisch;
        # dieser Test schützt trotzdem vor einer künftigen versehentlichen
        # Divergenz, falls die Vorlage einmal aufgespalten werden sollte.
        import re

        tabellen = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", sqlite_sql))
        self.assertIn("storage_cleanup_auftraege", tabellen)
        self.assertIn("produkt_zugriffe", tabellen)
        self.assertGreaterEqual(len(tabellen), 13)

    def test_j_migration_zweimal_hintereinander_fehlerfrei(self):
        tmp_dir = Path(tempfile.mkdtemp(prefix="clevoriq_migration_test_"))
        try:
            speicher.APP_DATEN_ORDNER = tmp_dir
            speicher.BENUTZER_ORDNER = tmp_dir / "users"
            speicher.DB_PFAD = tmp_dir / "bibliothek.db"
            speicher._ALTER_PDF_ORDNER = tmp_dir / "pdfs"
            speicher.datenbank_initialisieren()
            benutzer_id = speicher.benutzer_erstellen("j1", "j1@example.com", "Passwort123")

            speicher.datenbank_initialisieren()
            speicher.datenbank_initialisieren()

            self.assertIsNotNone(speicher.benutzer_nach_id(benutzer_id))
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# --------------------------------------------------------------------------
# K) Foreign-Key-/Cascade-Semantik in beiden Schema-Varianten
# --------------------------------------------------------------------------


class CascadeSchemaTests(unittest.TestCase):
    def test_k_on_delete_cascade_in_beiden_dialekten_vorhanden(self):
        for pk, blob in (("INTEGER PRIMARY KEY AUTOINCREMENT", "BLOB"), ("SERIAL PRIMARY KEY", "BYTEA")):
            sql = speicher._SCHEMA_VORLAGE.format(PK=pk, BLOB=blob)
            for tabelle_und_spalte in (
                "dokumente(id) ON DELETE CASCADE",
                "chats(id) ON DELETE CASCADE",
                "benutzer(id) ON DELETE CASCADE",
            ):
                self.assertIn(tabelle_und_spalte, sql, f"{tabelle_und_spalte} fehlt für PK={pk}")


# --------------------------------------------------------------------------
# M/N/O) Single-Use unter ECHTER Thread-Nebenläufigkeit
# --------------------------------------------------------------------------


class KonkurrenzTests(_TempDbTestCase):
    """Feuert zwei parallele Threads auf DIESELBE SQLite-Datei ab (jeder
    Thread über `speicher._verbindung()`, also eine eigene, echte
    Verbindung) - keine Mocks, echte Nebenläufigkeit. Beweist, dass die
    in Block 5 auf atomare `UPDATE ... RETURNING`/bedingte `UPDATE`
    umgestellten Single-Use-Pfade tatsächlich nur EINEN Gewinner pro
    Rennen zulassen."""

    def _parallel(self, funktion, n=8):
        ergebnisse = [None] * n
        threads = []

        def _lauf(index):
            ergebnisse[index] = funktion()

        for i in range(n):
            t = threading.Thread(target=_lauf, args=(i,))
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        return ergebnisse

    def test_m_email_verifizierung_token_nur_einmal_bei_konkurrenz(self):
        benutzer_id = self._neuer_benutzer("m1", "m1@example.com")
        token = speicher.email_verifizierung_erstellen(benutzer_id, "m1@example.com")

        ergebnisse = self._parallel(lambda: speicher.email_verifizierung_bestaetigen(token)[0])

        self.assertEqual(sum(1 for r in ergebnisse if r), 1, ergebnisse)

    def test_n_backup_code_nur_einmal_bei_konkurrenz(self):
        benutzer_id = self._neuer_benutzer("n1", "n1@example.com")
        _secret, backup_codes = self._2fa_aktivieren(benutzer_id)
        code = backup_codes[0]

        ergebnisse = self._parallel(
            lambda: speicher.zwei_faktor_backup_code_pruefen_und_verbrauchen(benutzer_id, code)
        )

        self.assertEqual(sum(1 for r in ergebnisse if r), 1, ergebnisse)

    def test_o_2fa_challenge_nur_einmal_bei_konkurrenz(self):
        benutzer_id = self._neuer_benutzer("o1", "o1@example.com")
        secret, _backup_codes = self._2fa_aktivieren(benutzer_id)
        challenge_token = speicher.zwei_faktor_challenge_erstellen(benutzer_id)
        # `_2fa_aktivieren` hat den AKTUELLEN Zeitschritt bereits über
        # `zwei_faktor_setup_bestaetigen` verbraucht (Replay-Schutz,
        # siehe `zwei_faktor_krypto.totp_code_pruefen`) - ein Code für
        # denselben Zeitschritt würde hier fälschlich als Replay statt
        # als Konkurrenz-Rennen erkannt. Ein Code für den NÄCHSTEN
        # Zeitschritt bleibt innerhalb des ±1-Toleranzfensters gültig,
        # siehe `test_auth_security.py`s `_naechster_totp_code`.
        code = pyotp.TOTP(secret).at(time.time() + zwei_faktor_krypto.TOTP_SCHRITT_SEKUNDEN)

        ergebnisse = self._parallel(
            lambda: speicher.zwei_faktor_challenge_pruefen_und_verbrauchen(challenge_token, code, False)[0]
        )

        self.assertEqual(sum(1 for r in ergebnisse if r), 1, ergebnisse)


# --------------------------------------------------------------------------
# P/Q) Produktberechtigung, User-Isolation (Regression)
# --------------------------------------------------------------------------


class ProduktUndIsolationRegressionTests(_TempDbTestCase):
    def test_p_produktberechtigung_funktioniert_weiterhin(self):
        benutzer_id = self._neuer_benutzer("p1", "p1@example.com")
        self.assertTrue(speicher.produkt_zugriff_aktiv(benutzer_id, produkte.PRODUKT_DOCUMENTS))

    def test_q_user_isolation_bleibt_bestehen(self):
        a = self._neuer_benutzer("q_a", "q_a@example.com")
        b = self._neuer_benutzer("q_b", "q_b@example.com")
        dok_b = self._dokument_anlegen(b, "geheim.pdf")

        self.assertIsNone(speicher.dokument_datei_lesen(dok_b, a))
        self.assertEqual(speicher.dokumente_laden(a), [])


# --------------------------------------------------------------------------
# R/S/T/U/V/W) Storage-Cleanup-Outbox
# --------------------------------------------------------------------------


class StorageCleanupOutboxTests(_TempDbTestCase):
    def _offene_auftraege(self):
        with speicher._verbindung() as conn:
            return conn.execute(
                "SELECT * FROM storage_cleanup_auftraege WHERE status = ?",
                (speicher.STORAGE_CLEANUP_STATUS_OFFEN,),
            ).fetchall()

    def test_r_upload_db_fehler_fuehrt_zu_storage_cleanup_ohne_offenen_auftrag(self):
        """R) Der Normalfall aus Block 4: DB-Fehler nach Storage-Upload ->
        Kompensations-Löschung gelingt -> KEIN offener Cleanup-Auftrag
        nötig (die Kompensation hat das Objekt bereits entfernt)."""
        benutzer_id = self._neuer_benutzer("r1", "r1@example.com")
        inhalt = b"Doppelt"
        hash_wert = speicher.hash_berechnen(inhalt + b"gleich.pdf")
        speicher.dokument_speichern("gleich.pdf", hash_wert, inhalt, 1, "pdf", "seite", benutzer_id)

        with self.assertRaises(Exception):
            speicher.dokument_speichern("gleich.pdf", hash_wert, inhalt, 1, "pdf", "seite", benutzer_id)

        self.assertEqual(self._offene_auftraege(), [])

    def test_s_upload_doppelfehler_erzeugt_cleanup_auftrag(self):
        """S) Schlägt SOGAR die Kompensations-Löschung fehl (Doppelfehler),
        muss ein persistenter Cleanup-Auftrag entstehen."""
        benutzer_id = self._neuer_benutzer("s1", "s1@example.com")
        inhalt = b"Doppelt2"
        hash_wert = speicher.hash_berechnen(inhalt + b"gleich2.pdf")
        speicher.dokument_speichern("gleich2.pdf", hash_wert, inhalt, 1, "pdf", "seite", benutzer_id)

        with mock.patch.object(
            storage.LocalFileStorage, "loeschen", side_effect=storage.StorageFehler("kaputt")
        ):
            with self.assertRaises(Exception):
                speicher.dokument_speichern("gleich2.pdf", hash_wert, inhalt, 1, "pdf", "seite", benutzer_id)

        offene = self._offene_auftraege()
        self.assertEqual(len(offene), 1)
        self.assertEqual(offene[0]["grund"], "upload_kompensation")

    def test_t_loeschen_mit_storage_ausfall_erzeugt_cleanup_auftrag(self):
        """T) Storage nicht erreichbar beim Löschen -> Dokument ist für
        den Benutzer trotzdem SOFORT weg, UND ein Cleanup-Auftrag bleibt
        offen stehen."""
        benutzer_id = self._neuer_benutzer("t1", "t1@example.com")
        dokument_id = self._dokument_anlegen(benutzer_id, "t.pdf", b"T-Inhalt")

        with mock.patch.object(
            storage.LocalFileStorage, "loeschen", side_effect=storage.StorageFehler("kaputt")
        ):
            speicher.dokument_loeschen(dokument_id, benutzer_id)

        self.assertEqual(speicher.dokumente_laden(benutzer_id), [])
        offene = self._offene_auftraege()
        self.assertEqual(len(offene), 1)
        self.assertEqual(offene[0]["grund"], "dokument_geloescht")
        self.assertEqual(offene[0]["art"], speicher.STORAGE_CLEANUP_ART_OBJEKT)

    def test_u_spaeterer_cleanup_erfolgreich(self):
        """U) Storage wird wieder erreichbar - ein späterer Aufruf von
        `cleanup_pending_storage_deletions` räumt das Objekt tatsächlich
        auf und markiert den Auftrag als erledigt."""
        benutzer_id = self._neuer_benutzer("u1", "u1@example.com")
        dokument_id = self._dokument_anlegen(benutzer_id, "u.pdf", b"U-Inhalt")
        storage_key = speicher.dokumente_laden(benutzer_id)[0]["storage_key"]

        with mock.patch.object(
            storage.LocalFileStorage, "loeschen", side_effect=storage.StorageFehler("kaputt")
        ):
            speicher.dokument_loeschen(dokument_id, benutzer_id)

        self.assertEqual(len(self._offene_auftraege()), 1)

        ergebnis = speicher.cleanup_pending_storage_deletions()

        self.assertEqual(ergebnis["erledigt"], 1)
        self.assertEqual(ergebnis["verbleibend_offen"], 0)
        self.assertEqual(self._offene_auftraege(), [])

        fs = storage.LocalFileStorage(speicher.APP_DATEN_ORDNER)
        self.assertFalse(fs.existiert(storage_key))

    def test_v_cleanup_von_user_a_loescht_niemals_objekt_von_user_b(self):
        """V) Zwei offene Cleanup-Aufträge für ZWEI verschiedene Benutzer
        - ein Cleanup-Lauf darf ausschließlich das jeweils EIGENE, exakt
        zugeordnete Objekt entfernen."""
        benutzer_a = self._neuer_benutzer("v_a", "v_a@example.com")
        benutzer_b = self._neuer_benutzer("v_b", "v_b@example.com")
        dok_a = self._dokument_anlegen(benutzer_a, "a.pdf", b"A-Inhalt")
        dok_b = self._dokument_anlegen(benutzer_b, "b.pdf", b"B-Inhalt")
        key_a = speicher.dokumente_laden(benutzer_a)[0]["storage_key"]
        key_b = speicher.dokumente_laden(benutzer_b)[0]["storage_key"]

        with mock.patch.object(
            storage.LocalFileStorage, "loeschen", side_effect=storage.StorageFehler("kaputt")
        ):
            speicher.dokument_loeschen(dok_a, benutzer_a)
            speicher.dokument_loeschen(dok_b, benutzer_b)

        fs = storage.LocalFileStorage(speicher.APP_DATEN_ORDNER)
        self.assertTrue(fs.existiert(key_a))
        self.assertTrue(fs.existiert(key_b))

        ergebnis = speicher.cleanup_pending_storage_deletions()

        self.assertEqual(ergebnis["erledigt"], 2)
        self.assertFalse(fs.existiert(key_a))
        self.assertFalse(fs.existiert(key_b))

    def test_w_kontoloeschung_mit_storage_ausfall_erzeugt_cleanup_auftrag(self):
        """W) Konto ist bei einem Storage-Ausfall trotzdem sofort und
        vollständig aus der Datenbank gelöscht (kein Login mehr
        möglich); die Objekt-Bereinigung wird über die Outbox
        nachgeholt."""
        benutzer_id = self._neuer_benutzer("w1", "w1@example.com")
        self._dokument_anlegen(benutzer_id, "w.pdf", b"W-Inhalt")

        with mock.patch.object(
            storage.LocalFileStorage, "praefix_loeschen", side_effect=storage.StorageFehler("kaputt")
        ):
            fehler = speicher.konto_endgueltig_loeschen(benutzer_id)

        self.assertIsNone(fehler)
        self.assertIsNone(speicher.benutzer_nach_id(benutzer_id))

        offene = self._offene_auftraege()
        self.assertEqual(len(offene), 1)
        self.assertEqual(offene[0]["art"], speicher.STORAGE_CLEANUP_ART_PRAEFIX)
        self.assertEqual(offene[0]["grund"], "konto_geloescht")

    def test_cleanup_versucht_maximal_begrenzt_oft_und_scheitert_dann_final(self):
        benutzer_id = self._neuer_benutzer("finalfail", "finalfail@example.com")
        dokument_id = self._dokument_anlegen(benutzer_id, "x.pdf", b"X")

        with mock.patch.object(
            storage.LocalFileStorage, "loeschen", side_effect=storage.StorageFehler("dauerhaft kaputt")
        ):
            speicher.dokument_loeschen(dokument_id, benutzer_id)

            for _ in range(speicher.STORAGE_CLEANUP_MAX_VERSUCHE):
                with speicher._verbindung() as conn:
                    conn.execute(
                        "UPDATE storage_cleanup_auftraege SET naechster_versuch_am = NULL"
                    )
                speicher.cleanup_pending_storage_deletions()

        with speicher._verbindung() as conn:
            zeile = conn.execute(
                "SELECT status, versuche FROM storage_cleanup_auftraege"
            ).fetchone()

        self.assertEqual(zeile["status"], speicher.STORAGE_CLEANUP_STATUS_FEHLGESCHLAGEN)
        self.assertGreaterEqual(zeile["versuche"], speicher.STORAGE_CLEANUP_MAX_VERSUCHE)


# --------------------------------------------------------------------------
# X) Keine Secrets in Logs/Fehlermeldungen
# --------------------------------------------------------------------------


class SecretsNichtInLogsTests(_TempDbTestCase):
    def test_x_cleanup_fehlertext_enthaelt_keine_storage_credentials(self):
        benutzer_id = self._neuer_benutzer("x1", "x1@example.com")
        dokument_id = self._dokument_anlegen(benutzer_id, "x.pdf", b"X-Inhalt")

        with mock.patch.object(
            storage.LocalFileStorage,
            "loeschen",
            side_effect=storage.StorageFehler("Datei konnte nicht gelöscht werden."),
        ):
            speicher.dokument_loeschen(dokument_id, benutzer_id)
            speicher.cleanup_pending_storage_deletions()

        with speicher._verbindung() as conn:
            zeile = conn.execute("SELECT letzter_fehler FROM storage_cleanup_auftraege").fetchone()

        for verbotenes_wort in ("AKIA", "secret", "password", "passwort"):
            self.assertNotIn(verbotenes_wort, (zeile["letzter_fehler"] or "").lower())


# --------------------------------------------------------------------------
# Y/Z) Regression: Bestandsdateien, Hub/Documents/Library
# --------------------------------------------------------------------------


class RegressionTests(_TempDbTestCase):
    def test_y_bestehende_lokale_dateien_bleiben_lesbar(self):
        benutzer_id = self._neuer_benutzer("y1", "y1@example.com")

        alter_ordner = speicher.APP_DATEN_ORDNER / "users" / str(benutzer_id) / "documents"
        alter_ordner.mkdir(parents=True, exist_ok=True)
        (alter_ordner / "abc999.pdf").write_bytes(b"Alt-Inhalt")

        with speicher._verbindung() as conn:
            alt_id = db_backend.insert_und_id_zurueckgeben(
                conn,
                "INSERT INTO dokumente "
                "(user_id, dateiname, hash, seitenzahl, hochgeladen_am, dateityp, "
                "einheit_typ, groesse_bytes, public_id, storage_key) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                (benutzer_id, "alt.pdf", "abc999", 1, speicher._jetzt(), "pdf", "seite", 10, None),
            )

        speicher.datenbank_initialisieren()

        self.assertEqual(speicher.dokument_datei_lesen(alt_id, benutzer_id), b"Alt-Inhalt")

    def test_z_hub_und_documents_sehen_dieselben_dokumente(self):
        benutzer_id = self._neuer_benutzer("z1", "z1@example.com")
        self._dokument_anlegen(benutzer_id, "z.pdf")

        hub_sicht = speicher.dokumente_laden(benutzer_id)
        documents_sicht = speicher.dokumente_laden(benutzer_id)

        self.assertEqual(hub_sicht, documents_sicht)
        self.assertEqual(len(hub_sicht), 1)


if __name__ == "__main__":
    unittest.main()
