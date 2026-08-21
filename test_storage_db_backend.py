"""Automatisierte Tests für Block 4 (PostgreSQL-/Object-Storage-Vorbereitung).

Deckt `storage.py` (LocalFileStorage/S3Storage-Abstraktion), `db_backend.py`
(SQLite-/PostgreSQL-Verbindungsabstraktion) und deren Integration in
`speicher.py` (Upload-/Lösch-Konsistenz, Storage-Key statt lokalem Pfad,
User-Isolation) ab.

Bewusst wie `test_auth_security.py`/`test_hub_produkte.py` ein reines
`unittest`-Skript auf einer frischen, temporären SQLite-Datenbank je
Test. Macht KEINE echte AWS-/IONOS-Verbindung (S3Storage wird gegen
einen im Test hand-geschriebenen Fake-S3-Client getestet, siehe
`_FakeS3Client`) und KEINE echte PostgreSQL-Verbindung (`db_backend`
wird gegen ein Fake-`psycopg2`-Modul getestet, siehe `_FakePsycopg2`).
Macht keine echten OpenAI-Aufrufe (Chunks werden mit handgeschriebenen
Fake-Embeddings gespeichert) und keine echten E-Mails.

Ausführen mit:

    python test_storage_db_backend.py

oder:

    python -m unittest test_storage_db_backend
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ["CLEVORIQ_EMAIL_PROVIDER"] = "dev"
os.environ.pop("AVENLOQ_EMAIL_PROVIDER", None)
os.environ.pop("RESEND_API_KEY", None)

import db_backend  # noqa: E402
import speicher  # noqa: E402
import storage  # noqa: E402


# --------------------------------------------------------------------------
# Fake-S3-Client (siehe storage.py's `S3Storage`) - implementiert genau die
# Teilmenge der boto3-`s3`-Client-Schnittstelle, die `storage.S3Storage`
# nutzt, komplett in-memory. Keine echte AWS-/IONOS-Verbindung nötig.
# --------------------------------------------------------------------------


class _FakeClientFehler(Exception):
    def __init__(self, code):
        self.response = {"Error": {"Code": code}}
        super().__init__(code)


class _FakeS3Body:
    def __init__(self, daten):
        self._daten = daten

    def read(self):
        return self._daten


class _FakePaginator:
    def __init__(self, objekte):
        self._objekte = objekte

    def paginate(self, Bucket, Prefix):
        treffer = [schluessel for schluessel in self._objekte if schluessel.startswith(Prefix)]
        yield {"Contents": [{"Key": schluessel} for schluessel in treffer]}


class _FakeS3Client:
    """In-memory-Ersatz für einen boto3-`s3`-Client - siehe Moduldocstring."""

    def __init__(self):
        self._objekte = {}
        self.exceptions = SimpleNamespace(NoSuchKey=_FakeClientFehler)

    def put_object(self, Bucket, Key, Body):
        self._objekte[Key] = bytes(Body)

    def get_object(self, Bucket, Key):
        if Key not in self._objekte:
            raise _FakeClientFehler("NoSuchKey")
        return {"Body": _FakeS3Body(self._objekte[Key])}

    def head_object(self, Bucket, Key):
        if Key not in self._objekte:
            raise _FakeClientFehler("404")
        return {"ContentLength": len(self._objekte[Key])}

    def delete_object(self, Bucket, Key):
        self._objekte.pop(Key, None)

    def delete_objects(self, Bucket, Delete):
        for eintrag in Delete["Objects"]:
            self._objekte.pop(eintrag["Key"], None)

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return _FakePaginator(self._objekte)

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        return f"https://fake-s3.example.invalid/{Params['Bucket']}/{Params['Key']}?exp={ExpiresIn}"


def _fake_s3_storage():
    """Baut ein `storage.S3Storage` mit `_FakeS3Client` statt einer echten
    boto3-Verbindung - keine echten Zugangsdaten/kein Netzwerk nötig."""
    with mock.patch.object(storage.S3Storage, "_client_erstellen", return_value=_FakeS3Client()):
        return storage.S3Storage("http://fake-endpoint.invalid", "eu-central", "test-bucket", "AKIAFAKE", "secret")


# --------------------------------------------------------------------------
# T) storage_key_gueltig / Path-Traversal
# --------------------------------------------------------------------------


class StorageKeyValidierungTests(unittest.TestCase):
    def test_i_gueltige_keys_akzeptiert(self):
        for key in ("users/3/documents/abc/original.pdf", "a", "users/1/documents/x.y-z_1"):
            self.assertTrue(storage.storage_key_gueltig(key), key)

    def test_i_path_traversal_abgelehnt(self):
        boesartig = [
            "../etc/passwd",
            "users/../../../etc/passwd",
            "users/3/../../../etc/passwd",
            "..",
            "a/../b",
        ]
        for key in boesartig:
            self.assertFalse(storage.storage_key_gueltig(key), key)

    def test_i_absolute_pfade_abgelehnt(self):
        for key in ("/etc/passwd", "\\Windows\\System32", "C:\\Windows\\x", "/users/3/x"):
            self.assertFalse(storage.storage_key_gueltig(key), key)

    def test_i_leer_oder_none_abgelehnt(self):
        self.assertFalse(storage.storage_key_gueltig(""))
        self.assertFalse(storage.storage_key_gueltig(None))


# --------------------------------------------------------------------------
# B) LocalFileStorage
# --------------------------------------------------------------------------


class LocalFileStorageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="clevoriq_storage_test_"))
        self.fs = storage.LocalFileStorage(self._tmp)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_b_speichern_lesen_existiert_groesse_loeschen(self):
        key = "users/1/documents/abc/original.pdf"

        self.assertFalse(self.fs.existiert(key))
        self.assertIsNone(self.fs.lesen(key))
        self.assertIsNone(self.fs.groesse(key))

        self.fs.speichern(key, b"Inhalt123")

        self.assertTrue(self.fs.existiert(key))
        self.assertEqual(self.fs.lesen(key), b"Inhalt123")
        self.assertEqual(self.fs.groesse(key), len(b"Inhalt123"))

        self.fs.loeschen(key)

        self.assertFalse(self.fs.existiert(key))
        self.assertIsNone(self.fs.lesen(key))

    def test_b_loeschen_nicht_existierender_key_ist_kein_fehler(self):
        self.fs.loeschen("users/1/documents/nie-vorhanden/original.pdf")

    def test_i_path_traversal_wird_beim_zugriff_blockiert(self):
        for key in ("../ausserhalb.txt", "/etc/passwd", "users/../../ausserhalb.txt"):
            with self.assertRaises(storage.StorageFehler):
                self.fs.speichern(key, b"x")
            with self.assertRaises(storage.StorageFehler):
                self.fs.lesen(key)

        # Es darf tatsächlich NICHTS außerhalb des Basisordners entstanden sein.
        self.assertEqual(list(self._tmp.parent.glob("ausserhalb.txt")), [])

    def test_praefix_loeschen(self):
        self.fs.speichern("users/3/documents/a/original.pdf", b"1")
        self.fs.speichern("users/3/documents/b/original.pdf", b"2")
        self.fs.speichern("users/4/documents/c/original.pdf", b"3")

        self.fs.praefix_loeschen("users/3/")

        self.assertFalse(self.fs.existiert("users/3/documents/a/original.pdf"))
        self.assertFalse(self.fs.existiert("users/3/documents/b/original.pdf"))
        self.assertTrue(self.fs.existiert("users/4/documents/c/original.pdf"))


# --------------------------------------------------------------------------
# T) S3Storage - Fake-Client, keine echte Cloud-Verbindung
# --------------------------------------------------------------------------


class S3StorageTests(unittest.TestCase):
    def setUp(self):
        self.s3 = _fake_s3_storage()

    def test_t_speichern_lesen_existiert_groesse_loeschen(self):
        key = "users/1/documents/abc/original.pdf"

        self.assertFalse(self.s3.existiert(key))
        self.assertIsNone(self.s3.lesen(key))
        self.assertIsNone(self.s3.groesse(key))

        self.s3.speichern(key, b"S3-Inhalt")

        self.assertTrue(self.s3.existiert(key))
        self.assertEqual(self.s3.lesen(key), b"S3-Inhalt")
        self.assertEqual(self.s3.groesse(key), len(b"S3-Inhalt"))

        self.s3.loeschen(key)

        self.assertFalse(self.s3.existiert(key))

    def test_t_loeschen_nicht_existierender_key_ist_kein_fehler(self):
        self.s3.loeschen("users/1/documents/nie-vorhanden/original.pdf")

    def test_t_praefix_loeschen(self):
        self.s3.speichern("users/3/documents/a/original.pdf", b"1")
        self.s3.speichern("users/3/documents/b/original.pdf", b"2")
        self.s3.speichern("users/4/documents/c/original.pdf", b"3")

        self.s3.praefix_loeschen("users/3/")

        self.assertFalse(self.s3.existiert("users/3/documents/a/original.pdf"))
        self.assertTrue(self.s3.existiert("users/4/documents/c/original.pdf"))

    def test_t_presigned_url_nur_nach_erfolgreichem_aufruf_erzeugt(self):
        key = "users/1/documents/abc/original.pdf"
        self.s3.speichern(key, b"x")

        url = self.s3.presigned_download_url(key, ablauf_sekunden=30)

        self.assertIn("test-bucket", url)
        self.assertIn(key, url)
        self.assertIn("exp=30", url)

    def test_i_path_traversal_wird_vor_jedem_client_aufruf_blockiert(self):
        for key in ("../ausserhalb.txt", "/etc/passwd"):
            with self.assertRaises(storage.StorageFehler):
                self.s3.speichern(key, b"x")
            with self.assertRaises(storage.StorageFehler):
                self.s3.presigned_download_url(key)


class S3KonfigurationTests(unittest.TestCase):
    def setUp(self):
        self._alte_werte = {
            name: os.environ.pop(name, None)
            for name in (
                "CLEVORIQ_STORAGE_BACKEND",
                "CLEVORIQ_S3_ENDPOINT",
                "CLEVORIQ_S3_REGION",
                "CLEVORIQ_S3_BUCKET",
                "CLEVORIQ_S3_ACCESS_KEY_ID",
                "CLEVORIQ_S3_SECRET_ACCESS_KEY",
            )
        }

    def tearDown(self):
        for name, wert in self._alte_werte.items():
            if wert is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = wert

    def test_s_fehlende_s3_variablen_nennen_nur_namen_keine_werte(self):
        os.environ["CLEVORIQ_STORAGE_BACKEND"] = "s3"
        os.environ["CLEVORIQ_S3_ACCESS_KEY_ID"] = "AKIA-GEHEIM-WERT"

        with self.assertRaises(storage.StorageFehler) as kontext:
            storage.storage_backend("/irrelevant")

        meldung = str(kontext.exception)
        self.assertIn("CLEVORIQ_S3_ENDPOINT", meldung)
        self.assertIn("CLEVORIQ_S3_BUCKET", meldung)
        # Der tatsächliche (gesetzte) Geheimwert darf NIE in der Meldung stehen.
        self.assertNotIn("AKIA-GEHEIM-WERT", meldung)

    def test_unbekanntes_backend_wird_klar_abgelehnt(self):
        os.environ["CLEVORIQ_STORAGE_BACKEND"] = "azure-blob"

        with self.assertRaises(storage.StorageFehler):
            storage.storage_backend("/irrelevant")

    def test_lokales_backend_bleibt_standard(self):
        self.assertEqual(storage.aktuelles_backend(), storage.BACKEND_LOCAL)


# --------------------------------------------------------------------------
# A) SQLite-Backend, PostgreSQL-Verbindungsaufbau (Fake-psycopg2)
# --------------------------------------------------------------------------


class _FakePsycopg2Cursor:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        self._letztes_sql = sql

    def fetchone(self):
        return {"ok": 1}

    def close(self):
        pass


class _FakePsycopg2Connection:
    def __init__(self, dsn):
        self.dsn = dsn
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return _FakePsycopg2Cursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class _FakePsycopg2Modul:
    """Ersetzt das echte `psycopg2`-Paket für Tests - keine echte
    PostgreSQL-Verbindung nötig (siehe `db_backend._lade_psycopg2`)."""

    class extras:
        RealDictCursor = object()

    @staticmethod
    def connect(dsn, cursor_factory=None):
        if "fehlschlagen" in dsn:
            raise RuntimeError("connection refused")
        return _FakePsycopg2Connection(dsn)


class DbBackendTests(unittest.TestCase):
    def setUp(self):
        self._alter_wert = os.environ.pop("CLEVORIQ_DATABASE_BACKEND", None)
        self._alte_url = os.environ.pop("CLEVORIQ_DATABASE_URL", None)

    def tearDown(self):
        if self._alter_wert is None:
            os.environ.pop("CLEVORIQ_DATABASE_BACKEND", None)
        else:
            os.environ["CLEVORIQ_DATABASE_BACKEND"] = self._alter_wert

        if self._alte_url is None:
            os.environ.pop("CLEVORIQ_DATABASE_URL", None)
        else:
            os.environ["CLEVORIQ_DATABASE_URL"] = self._alte_url

    def test_a_sqlite_ist_default_backend(self):
        self.assertEqual(db_backend.aktuelles_backend(), db_backend.BACKEND_SQLITE)

    def test_a_sqlite_verbindung_funktioniert(self):
        tmp = Path(tempfile.mkdtemp(prefix="clevoriq_db_test_")) / "test.db"
        try:
            with db_backend.sqlite_verbindung(tmp) as conn:
                conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, wert TEXT)")
                conn.execute("INSERT INTO t (wert) VALUES (?)", ("hallo",))

            with db_backend.sqlite_verbindung(tmp) as conn:
                zeile = conn.execute("SELECT wert FROM t").fetchone()
                self.assertEqual(zeile["wert"], "hallo")
        finally:
            shutil.rmtree(tmp.parent, ignore_errors=True)

    def test_postgresql_verbindung_ueber_fake_modul(self):
        with mock.patch.object(db_backend, "_lade_psycopg2", return_value=_FakePsycopg2Modul):
            with db_backend.postgresql_verbindung("postgresql://fake/db") as conn:
                self.assertIsInstance(conn, _FakePsycopg2Connection)
                conn.cursor().execute("SELECT 1")

            self.assertTrue(conn.committed)

    def test_s_postgresql_verbindungsfehler_nennt_nie_die_dsn(self):
        with mock.patch.object(db_backend, "_lade_psycopg2", return_value=_FakePsycopg2Modul):
            with self.assertRaises(db_backend.DatenbankFehler) as kontext:
                with db_backend.postgresql_verbindung("postgresql://geheimer:passwort@fehlschlagen"):
                    pass

        meldung = str(kontext.exception)
        self.assertNotIn("geheimer", meldung)
        self.assertNotIn("passwort", meldung)

    def test_postgresql_ohne_database_url_scheitert_klar(self):
        with self.assertRaises(db_backend.DatenbankFehler):
            with db_backend.postgresql_verbindung(""):
                pass

    def test_verbindung_dispatcht_nach_backend(self):
        os.environ["CLEVORIQ_DATABASE_BACKEND"] = "postgresql"
        os.environ["CLEVORIQ_DATABASE_URL"] = "postgresql://fake/db"

        with mock.patch.object(db_backend, "_lade_psycopg2", return_value=_FakePsycopg2Modul):
            with db_backend.verbindung("/irrelevant.db") as conn:
                self.assertIsInstance(conn, _FakePsycopg2Connection)

    def test_unbekanntes_backend_wird_klar_abgelehnt(self):
        os.environ["CLEVORIQ_DATABASE_BACKEND"] = "oracle"

        with self.assertRaises(db_backend.DatenbankFehler):
            with db_backend.verbindung("/irrelevant.db"):
                pass

    def test_datenbank_initialisieren_scheitert_klar_bei_postgresql(self):
        os.environ["CLEVORIQ_DATABASE_BACKEND"] = "postgresql"
        os.environ["CLEVORIQ_DATABASE_URL"] = "postgresql://fake/db"

        with self.assertRaises(NotImplementedError):
            speicher.datenbank_initialisieren()


# --------------------------------------------------------------------------
# Speicher-/Storage-Integration (C-M, N, O, R)
# --------------------------------------------------------------------------


class _TempDbTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = Path(tempfile.mkdtemp(prefix="clevoriq_storage_db_test_"))
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


class UploadKonsistenzTests(_TempDbTestCase):
    def test_c_upload_nutzt_zentralen_storage_layer(self):
        benutzer_id = self._neuer_benutzer("upload", "upload@example.com")
        self._dokument_anlegen(benutzer_id, "a.pdf", b"Bytes-A")

        dokument = speicher.dokumente_laden(benutzer_id)[0]
        self.assertTrue(dokument["storage_key"].startswith(f"users/{benutzer_id}/documents/"))

        fs = storage.LocalFileStorage(speicher.APP_DATEN_ORDNER)
        self.assertTrue(fs.existiert(dokument["storage_key"]))
        self.assertEqual(fs.lesen(dokument["storage_key"]), b"Bytes-A")

    def test_d_lesen_nutzt_storage_layer(self):
        benutzer_id = self._neuer_benutzer("lesen", "lesen@example.com")
        dokument_id = self._dokument_anlegen(benutzer_id, "b.pdf", b"Bytes-B")

        self.assertEqual(speicher.dokument_datei_lesen(dokument_id, benutzer_id), b"Bytes-B")

    def test_e_loeschen_nutzt_storage_layer(self):
        benutzer_id = self._neuer_benutzer("loeschen", "loeschen@example.com")
        dokument_id = self._dokument_anlegen(benutzer_id, "c.pdf", b"Bytes-C")
        storage_key = speicher.dokumente_laden(benutzer_id)[0]["storage_key"]

        speicher.dokument_loeschen(dokument_id, benutzer_id)

        fs = storage.LocalFileStorage(speicher.APP_DATEN_ORDNER)
        self.assertFalse(fs.existiert(storage_key))
        self.assertIsNone(speicher.dokument_datei_lesen(dokument_id, benutzer_id))

    def test_f_kontoloeschung_entfernt_benutzerobjekte_ueber_storage_layer(self):
        benutzer_id = self._neuer_benutzer("konto", "konto@example.com")
        self._dokument_anlegen(benutzer_id, "d.pdf", b"Bytes-D")
        storage_key = speicher.dokumente_laden(benutzer_id)[0]["storage_key"]

        fehler = speicher.konto_endgueltig_loeschen(benutzer_id)

        self.assertIsNone(fehler)
        fs = storage.LocalFileStorage(speicher.APP_DATEN_ORDNER)
        self.assertFalse(fs.existiert(storage_key))

    def test_g_user_a_kann_storage_objekt_von_b_nicht_ueber_dokument_datei_lesen_laden(self):
        benutzer_a = self._neuer_benutzer("iso_a", "iso_a@example.com")
        benutzer_b = self._neuer_benutzer("iso_b", "iso_b@example.com")
        dokument_b_id = self._dokument_anlegen(benutzer_b, "geheim.pdf", b"Geheimnis")

        self.assertIsNone(speicher.dokument_datei_lesen(dokument_b_id, benutzer_a))

    def test_h_bekannter_storage_key_ohne_eigentuemerschaft_bleibt_ueber_api_unerreichbar(self):
        """H) Ein manipulierter/erratener `storage_key` gibt über die
        einzige, tatsächlich genutzte API (`speicher.dokument_datei_lesen`,
        immer `dokument_id` + `benutzer_id`, nie ein roher Storage-Key)
        keinen Zugriff - selbst wenn der Key (wie hier zu Testzwecken)
        korrekt bekannt ist und das darunterliegende Objekt real
        existiert."""
        benutzer_a = self._neuer_benutzer("h_a", "h_a@example.com")
        benutzer_b = self._neuer_benutzer("h_b", "h_b@example.com")
        dokument_b_id = self._dokument_anlegen(benutzer_b, "geheim2.pdf", b"Geheimnis2")
        storage_key_b = speicher.dokumente_laden(benutzer_b)[0]["storage_key"]

        # Das Objekt existiert wirklich und ist über den (ownership-losen)
        # Storage-Layer direkt lesbar - das ist erwartet, siehe
        # storage.py's Moduldocstring ("ein Storage-Key ist niemals eine
        # Zugriffsberechtigung").
        fs = storage.LocalFileStorage(speicher.APP_DATEN_ORDNER)
        self.assertEqual(fs.lesen(storage_key_b), b"Geheimnis2")

        # Die einzige von `speicher.py` angebotene, tatsächlich genutzte
        # Zugriffsfunktion prüft aber IMMER Eigentümerschaft zuerst und
        # nimmt nie einen rohen Storage-Key von außen entgegen.
        self.assertIsNone(speicher.dokument_datei_lesen(dokument_b_id, benutzer_a))
        self.assertFalse(hasattr(speicher, "dokument_nach_storage_key"))

    def test_j_storage_fehlschlag_hinterlaesst_keinen_erfolgreichen_upload(self):
        benutzer_id = self._neuer_benutzer("j", "j@example.com")

        with mock.patch.object(
            storage.LocalFileStorage, "speichern", side_effect=storage.StorageFehler("kaputt")
        ):
            with self.assertRaises(storage.StorageFehler):
                self._dokument_anlegen(benutzer_id, "wird-nie-gespeichert.pdf", b"X")

        self.assertEqual(speicher.dokumente_laden(benutzer_id), [])

    def test_k_db_fehler_nach_storage_upload_fuehrt_zu_cleanup(self):
        benutzer_id = self._neuer_benutzer("k", "k@example.com")
        inhalt = b"Gleicher-Inhalt"
        hash_wert = speicher.hash_berechnen(inhalt + b"gleich.pdf")

        erster_id = speicher.dokument_speichern("gleich.pdf", hash_wert, inhalt, 1, "pdf", "seite", benutzer_id)
        erster_key = speicher.dokumente_laden(benutzer_id)[0]["storage_key"]

        # Zweiter Versuch mit IDENTISCHEM (hash, user_id) verletzt die
        # UNIQUE(hash, user_id)-Constraint -> die DB-Transaktion schlägt
        # fehl, NACHDEM die Storage-Datei bereits geschrieben wurde.
        with self.assertRaises(Exception):
            speicher.dokument_speichern("gleich.pdf", hash_wert, inhalt, 1, "pdf", "seite", benutzer_id)

        # Nur EIN Dokument in der DB (der erste, erfolgreiche Upload).
        alle = speicher.dokumente_laden(benutzer_id)
        self.assertEqual(len(alle), 1)
        self.assertEqual(alle[0]["id"], erster_id)

        # Die Storage-Datei des ERSTEN (erfolgreichen) Uploads existiert
        # weiterhin ...
        fs = storage.LocalFileStorage(speicher.APP_DATEN_ORDNER)
        self.assertTrue(fs.existiert(erster_key))

        # ... aber es liegt KEINE zusätzliche, verwaiste Datei vom
        # fehlgeschlagenen zweiten Versuch mehr herum: pro Benutzer darf
        # nach dem fehlgeschlagenen zweiten Upload nur genau ein
        # Storage-Objekt existieren.
        dokumente_ordner = speicher.APP_DATEN_ORDNER / "users" / str(benutzer_id) / "documents"
        objekte = list(dokumente_ordner.rglob("original.*"))
        self.assertEqual(len(objekte), 1)

    def test_l_loeschfehler_werden_sicher_behandelt(self):
        benutzer_id = self._neuer_benutzer("l", "l@example.com")
        dokument_id = self._dokument_anlegen(benutzer_id, "e.pdf", b"Bytes-E")

        with mock.patch.object(
            storage.LocalFileStorage, "loeschen", side_effect=storage.StorageFehler("kaputt")
        ):
            # Darf NICHT werfen - die DB-Zeile (Quelle der Wahrheit) muss
            # trotzdem entfernt werden.
            speicher.dokument_loeschen(dokument_id, benutzer_id)

        self.assertEqual(speicher.dokumente_laden(benutzer_id), [])

    def test_m_bestehende_alt_dateien_funktionieren_weiterhin(self):
        """M) Simuliert ein Dokument aus der Zeit VOR dem Storage-Key-Feld:
        Datei liegt bereits an der alten, hash-benannten Stelle,
        `storage_key` ist NULL. Nach einer erneuten Migration muss die
        Datei OHNE Verschiebung über den Storage-Layer lesbar sein."""
        benutzer_id = self._neuer_benutzer("alt", "alt@example.com")

        alter_ordner = speicher.APP_DATEN_ORDNER / "users" / str(benutzer_id) / "documents"
        alter_ordner.mkdir(parents=True, exist_ok=True)
        (alter_ordner / "abc123.pdf").write_bytes(b"Alte-Datei")

        with speicher._verbindung() as conn:
            cursor = conn.execute(
                "INSERT INTO dokumente "
                "(user_id, dateiname, hash, seitenzahl, hochgeladen_am, dateityp, "
                "einheit_typ, groesse_bytes, public_id, storage_key) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                (benutzer_id, "alt.pdf", "abc123", 1, speicher._jetzt(), "pdf", "seite", 10, None),
            )
            alt_dokument_id = cursor.lastrowid

        self.assertIsNone(speicher.dokument_datei_lesen(alt_dokument_id, benutzer_id))

        speicher.datenbank_initialisieren()

        gelesen = speicher.dokument_datei_lesen(alt_dokument_id, benutzer_id)
        self.assertEqual(gelesen, b"Alte-Datei")

        # Die Datei wurde NICHT verschoben - sie liegt noch exakt dort,
        # wo sie schon vorher lag.
        self.assertTrue((alter_ordner / "abc123.pdf").exists())

    def test_n_bestehende_public_ids_bleiben_bei_storage_migration_stabil(self):
        benutzer_id = self._neuer_benutzer("stabil", "stabil@example.com")
        self._dokument_anlegen(benutzer_id, "f.pdf", b"Bytes-F")

        vorher = speicher.dokumente_laden(benutzer_id)[0]["public_id"]
        speicher.datenbank_initialisieren()
        nachher = speicher.dokumente_laden(benutzer_id)[0]["public_id"]

        self.assertEqual(vorher, nachher)

    def test_o_hub_und_documents_sehen_weiterhin_dieselben_dokumente(self):
        benutzer_id = self._neuer_benutzer("gleich", "gleich@example.com")
        self._dokument_anlegen(benutzer_id, "g.pdf", b"Bytes-G")

        erste_sicht = speicher.dokumente_laden(benutzer_id)
        zweite_sicht = speicher.dokumente_laden(benutzer_id)

        self.assertEqual(erste_sicht, zweite_sicht)

    def test_q_produktberechtigungen_bleiben_funktional(self):
        import produkte

        benutzer_id = self._neuer_benutzer("produkt", "produkt@example.com")
        self.assertTrue(speicher.produkt_zugriff_aktiv(benutzer_id, produkte.PRODUKT_DOCUMENTS))

    def test_r_migration_zweimal_hintereinander_fehlerfrei(self):
        benutzer_id = self._neuer_benutzer("doppelt", "doppelt@example.com")
        self._dokument_anlegen(benutzer_id, "h.pdf", b"Bytes-H")

        speicher.datenbank_initialisieren()
        speicher.datenbank_initialisieren()

        dokumente = speicher.dokumente_laden(benutzer_id)
        self.assertEqual(len(dokumente), 1)
        self.assertIsNotNone(dokumente[0]["storage_key"])


if __name__ == "__main__":
    unittest.main()
