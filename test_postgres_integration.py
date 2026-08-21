"""ECHTE PostgreSQL-Integrationstests - laufen NUR, wenn `CLEVORIQ_TEST_POSTGRES_URL`

gesetzt ist (siehe CLAUDE.md "PostgreSQL-Testvariable"). Ohne diese
Variable wird die gesamte Datei übersprungen (`unittest.skip`), NICHT
stillschweigend gegen SQLite ausgeführt - ein grüner Lauf ohne die
Variable bedeutet NICHT, dass PostgreSQL verifiziert wurde.

WICHTIG - Sicherheit:

- `CLEVORIQ_TEST_POSTGRES_URL` MUSS auf eine ISOLIERTE, LEERE Test-
  Datenbank zeigen - NIEMALS auf eine Produktionsdatenbank. Diese Datei
  legt Tabellen an (additiv, `CREATE TABLE IF NOT EXISTS` - siehe
  `speicher.datenbank_initialisieren`) und schreibt/löscht Testzeilen
  mit eindeutigen, zufälligen Testdaten (Präfix `pgtest_` +
  `uuid4()`-Suffix je Testlauf) - es wird NIE eine vorhandene Zeile
  einer echten Anwendung verändert oder gelöscht, und es wird NIE
  `DROP TABLE`/`TRUNCATE` verwendet.
- Diese Datei verändert NIEMALS `CLEVORIQ_DATABASE_BACKEND`/
  `CLEVORIQ_DATABASE_URL` dauerhaft - beide werden in `setUpModule`
  gesichert und in `tearDownModule` zurückgesetzt.
- Kein automatisches Installieren von PostgreSQL/Docker - wenn lokal
  nichts läuft, bleibt diese Datei einfach ein übersprungener Block
  (siehe CLAUDE.md "PostgreSQL noch nicht praktisch getestet").

Ausführen (nur mit einer laufenden, leeren PostgreSQL-Testinstanz):

    CLEVORIQ_TEST_POSTGRES_URL=postgresql://user:pass@localhost:5432/clevoriq_test \\
        python test_postgres_integration.py
"""

import os
import unittest
import uuid

os.environ["CLEVORIQ_EMAIL_PROVIDER"] = "dev"
os.environ.pop("AVENLOQ_EMAIL_PROVIDER", None)
os.environ.pop("RESEND_API_KEY", None)

_TEST_2FA_SCHLUESSEL = "yq3nD5wq0v1sO4kQe9ZfW2mC7bH8jU6xR1tL0nA5pY4="
os.environ["CLEVORIQ_2FA_ENCRYPTION_KEY"] = _TEST_2FA_SCHLUESSEL

_POSTGRES_URL = os.environ.get("CLEVORIQ_TEST_POSTGRES_URL", "").strip()

import db_backend  # noqa: E402
import produkte  # noqa: E402
import pyotp  # noqa: E402
import speicher  # noqa: E402

_ALTER_BACKEND = None
_ALTE_URL = None


def setUpModule():
    global _ALTER_BACKEND, _ALTE_URL
    _ALTER_BACKEND = os.environ.get("CLEVORIQ_DATABASE_BACKEND")
    _ALTE_URL = os.environ.get("CLEVORIQ_DATABASE_URL")

    if not _POSTGRES_URL:
        return

    os.environ["CLEVORIQ_DATABASE_BACKEND"] = db_backend.BACKEND_POSTGRESQL
    os.environ["CLEVORIQ_DATABASE_URL"] = _POSTGRES_URL
    speicher.datenbank_initialisieren()


def tearDownModule():
    if _ALTER_BACKEND is None:
        os.environ.pop("CLEVORIQ_DATABASE_BACKEND", None)
    else:
        os.environ["CLEVORIQ_DATABASE_BACKEND"] = _ALTER_BACKEND

    if _ALTE_URL is None:
        os.environ.pop("CLEVORIQ_DATABASE_URL", None)
    else:
        os.environ["CLEVORIQ_DATABASE_URL"] = _ALTE_URL


def _test_praefix():
    return f"pgtest_{uuid.uuid4().hex[:12]}"


@unittest.skipUnless(
    _POSTGRES_URL,
    "CLEVORIQ_TEST_POSTGRES_URL nicht gesetzt - echte PostgreSQL-Integrationstests "
    "übersprungen (siehe CLAUDE.md: PostgreSQL ist damit NICHT praktisch verifiziert).",
)
class PostgresIntegrationTests(unittest.TestCase):
    """Jede Testmethode nutzt einen EIGENEN, zufälligen Benutzernamen-/
    E-Mail-Präfix (siehe `_test_praefix`) - Tests kollidieren dadurch
    nicht miteinander und hinterlassen bei einem Abbruch höchstens
    harmlose, eindeutig als Testdaten erkennbare Zeilen, nie einen
    Zustand, der eine echte Anwendung beeinträchtigen könnte."""

    def _neuer_benutzer(self):
        praefix = _test_praefix()
        benutzer_id = speicher.benutzer_erstellen(praefix, f"{praefix}@example.com", "Passwort123")
        return benutzer_id, praefix

    def test_schema_frisch_erstellbar(self):
        # `setUpModule` hat dies bereits ausgeführt - hier zusätzlich
        # explizit als Testfall UND ein zweites Mal (Idempotenz).
        speicher.datenbank_initialisieren()
        speicher.datenbank_initialisieren()

    def test_benutzer_registrieren_login_sessions(self):
        benutzer_id, praefix = self._neuer_benutzer()

        geladen = speicher.benutzer_nach_login(praefix)
        self.assertIsNotNone(geladen)
        self.assertEqual(geladen["id"], benutzer_id)

        token = speicher.sitzung_erstellen(benutzer_id)
        gueltige_id = speicher.sitzung_pruefen_und_aktualisieren(token)
        self.assertEqual(gueltige_id, benutzer_id)

        speicher.sitzung_widerrufen(token)
        self.assertIsNone(speicher.sitzung_pruefen_und_aktualisieren(token))

    def test_email_verifikations_token(self):
        benutzer_id, praefix = self._neuer_benutzer()
        token = speicher.email_verifizierung_erstellen(benutzer_id, f"{praefix}@example.com")

        erfolg, _meldung, betroffene_id = speicher.email_verifizierung_bestaetigen(token)

        self.assertTrue(erfolg)
        self.assertEqual(betroffene_id, benutzer_id)
        # Einmalig verwendbar - zweiter Versuch schlägt fehl.
        erfolg2, _meldung2, _id2 = speicher.email_verifizierung_bestaetigen(token)
        self.assertFalse(erfolg2)

    def test_passwort_reset(self):
        benutzer_id, praefix = self._neuer_benutzer()
        token, konto_email = speicher.passwort_reset_anfordern(praefix)

        self.assertIsNotNone(token)
        self.assertEqual(konto_email, f"{praefix}@example.com")

        erfolg, _meldung, betroffene_id = speicher.passwort_reset_einloesen(token, "NeuesPasswort123")

        self.assertTrue(erfolg)
        self.assertEqual(betroffene_id, benutzer_id)

    def test_2fa_und_backup_code(self):
        benutzer_id, _praefix = self._neuer_benutzer()

        secret, _uri = speicher.zwei_faktor_setup_starten(benutzer_id)
        code = pyotp.TOTP(secret).now()
        erfolg, meldung, backup_codes = speicher.zwei_faktor_setup_bestaetigen(benutzer_id, code)

        self.assertTrue(erfolg, meldung)
        self.assertEqual(len(backup_codes), speicher.BACKUP_CODES_ANZAHL)

        verbraucht = speicher.zwei_faktor_backup_code_pruefen_und_verbrauchen(benutzer_id, backup_codes[0])
        self.assertTrue(verbraucht)

        nochmal = speicher.zwei_faktor_backup_code_pruefen_und_verbrauchen(benutzer_id, backup_codes[0])
        self.assertFalse(nochmal)

    def test_produktberechtigung(self):
        benutzer_id, _praefix = self._neuer_benutzer()
        self.assertTrue(speicher.produkt_zugriff_aktiv(benutzer_id, produkte.PRODUKT_DOCUMENTS))

    def test_dokument_metadaten_public_id_storage_key(self):
        benutzer_id, _praefix = self._neuer_benutzer()
        inhalt = f"Inhalt-{uuid.uuid4()}".encode("utf-8")
        hash_wert = speicher.hash_berechnen(inhalt)

        dokument_id = speicher.dokument_speichern(
            "test.pdf", hash_wert, inhalt, 1, "pdf", "seite", benutzer_id
        )

        dokumente = speicher.dokumente_laden(benutzer_id)
        self.assertEqual(len(dokumente), 1)
        self.assertIsNotNone(dokumente[0]["public_id"])
        self.assertTrue(dokumente[0]["storage_key"].startswith(f"users/{benutzer_id}/documents/"))
        self.assertEqual(speicher.dokument_datei_lesen(dokument_id, benutzer_id), inhalt)

    def test_chats_und_chunks(self):
        benutzer_id, _praefix = self._neuer_benutzer()
        inhalt = f"Chunk-Inhalt-{uuid.uuid4()}".encode("utf-8")
        hash_wert = speicher.hash_berechnen(inhalt)
        dokument_id = speicher.dokument_speichern(
            "chunk.pdf", hash_wert, inhalt, 1, "pdf", "seite", benutzer_id
        )
        speicher.chunks_speichern(
            dokument_id,
            [{"seitennummer": 1, "text": "Beispieltext", "einheit_typ": "seite", "einheit_anzeige": None}],
            [[0.1, 0.2, 0.3]],
        )

        chat_id = speicher.chat_erstellen(benutzer_id)
        speicher.chat_dokumente_setzen(chat_id, [dokument_id], benutzer_id)
        speicher.nachricht_hinzufuegen(chat_id, benutzer_id, "Frage?", "Antwort.", [])

        chat = speicher.chat_laden(chat_id, benutzer_id)
        self.assertEqual(chat["dokument_ids"], [dokument_id])
        self.assertEqual(len(chat["nachrichten"]), 1)

        chunks = speicher.chunks_laden([dokument_id], benutzer_id)
        self.assertEqual(len(chunks), 1)

    def test_delete_cascades(self):
        benutzer_id, _praefix = self._neuer_benutzer()
        inhalt = f"Cascade-{uuid.uuid4()}".encode("utf-8")
        hash_wert = speicher.hash_berechnen(inhalt)
        dokument_id = speicher.dokument_speichern(
            "cascade.pdf", hash_wert, inhalt, 1, "pdf", "seite", benutzer_id
        )
        speicher.chunks_speichern(
            dokument_id,
            [{"seitennummer": 1, "text": "x", "einheit_typ": "seite", "einheit_anzeige": None}],
            [[0.1, 0.2, 0.3]],
        )
        chat_id = speicher.chat_erstellen(benutzer_id)
        speicher.nachricht_hinzufuegen(chat_id, benutzer_id, "F", "A", [])

        fehler = speicher.konto_endgueltig_loeschen(benutzer_id)

        self.assertIsNone(fehler)
        self.assertIsNone(speicher.benutzer_nach_id(benutzer_id))
        self.assertEqual(speicher.dokumente_laden(benutzer_id), [])
        self.assertIsNone(speicher.chat_laden(chat_id, benutzer_id))

    def test_transaktion_rollback_bei_integritaetsfehler(self):
        benutzer_id, praefix = self._neuer_benutzer()

        with self.assertRaises(Exception):
            speicher.benutzer_erstellen(praefix, f"{praefix}-andere@example.com", "Passwort123")

        # Der fehlgeschlagene zweite Versuch darf keine halbe Zeile
        # hinterlassen haben (Rollback der gesamten Transaktion,
        # inklusive des `produkt_zugriffe`-Inserts in `benutzer_erstellen`).
        with db_backend.verbindung("unbenutzt") as conn:
            anzahl = conn.execute(
                "SELECT COUNT(*) AS anzahl FROM benutzer WHERE benutzername = ?", (praefix,)
            ).fetchone()["anzahl"]

        self.assertEqual(anzahl, 1)

    def test_user_isolation(self):
        benutzer_a, _praefix_a = self._neuer_benutzer()
        benutzer_b, _praefix_b = self._neuer_benutzer()

        inhalt = f"Isoliert-{uuid.uuid4()}".encode("utf-8")
        hash_wert = speicher.hash_berechnen(inhalt)
        dokument_b = speicher.dokument_speichern(
            "b.pdf", hash_wert, inhalt, 1, "pdf", "seite", benutzer_b
        )

        self.assertIsNone(speicher.dokument_datei_lesen(dokument_b, benutzer_a))
        self.assertEqual(speicher.dokumente_laden(benutzer_a), [])


if __name__ == "__main__":
    unittest.main()
