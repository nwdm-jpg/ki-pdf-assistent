"""Automatisierte Tests für die Authentifizierungs-/Sicherheits-Härtung
(E-Mail-Verifizierung, Passwort-Reset, Rate-Limiting, Sitzungen, IDOR-
Schutz, Migrationssicherheit).

Bewusst als reines `unittest`-Skript (kein pytest - nicht in
`requirements.txt`, siehe CLAUDE.md "keine Test-Suite konfiguriert").
Ausführen mit:

    python test_auth_security.py

oder:

    python -m unittest test_auth_security

Testet AUSSCHLIESSLICH die Streamlit-unabhängigen Module (`speicher.py`,
`auth.py`, `ratenbegrenzung.py`, `email_versand.py`, `sicherheitslog.py`)
- die eigentliche UI-Orchestrierung in `benutzer.py`/`konto.py`/
`web_app.py` (Formulare, Sidebar-Gating) hängt von einem laufenden
Streamlit-Skriptkontext ab und ist damit nicht sinnvoll ohne echten
`streamlit run`-Lauf automatisiert testbar (siehe CLAUDE.md
"Testing approach") - sie wurde stattdessen per Code-Review geprüft.

WICHTIG: Erzwingt `CLEVORIQ_EMAIL_PROVIDER=dev` VOR jedem Import, damit
unter keinen Umständen ein echter Resend-Versand ausgelöst wird, egal
was in der Shell-Umgebung sonst gesetzt ist. Jeder Test arbeitet auf
einer frischen, temporären SQLite-Datenbank (kein Zugriff auf
`app_daten/` des echten Projekts) und macht KEINE echten OpenAI- oder
Resend-Aufrufe.
"""

import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

os.environ["CLEVORIQ_EMAIL_PROVIDER"] = "dev"
os.environ.pop("AVENLOQ_EMAIL_PROVIDER", None)
os.environ.pop("RESEND_API_KEY", None)

import auth  # noqa: E402
import email_versand  # noqa: E402
import ratenbegrenzung  # noqa: E402
import speicher  # noqa: E402


class _TempDbTestCase(unittest.TestCase):
    """Basisklasse: isoliert jede Testmethode auf einer frischen, temporären DB."""

    def setUp(self):
        self._tmp_dir = Path(tempfile.mkdtemp(prefix="clevoriq_test_"))
        speicher.APP_DATEN_ORDNER = self._tmp_dir
        speicher.BENUTZER_ORDNER = self._tmp_dir / "users"
        speicher.DB_PFAD = self._tmp_dir / "bibliothek.db"
        speicher._ALTER_PDF_ORDNER = self._tmp_dir / "pdfs"
        speicher.datenbank_initialisieren()
        email_versand.GESENDETE_ENTWICKLUNGS_MAILS.clear()

    def tearDown(self):
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _neuer_benutzer(self, benutzername="testuser", email="test@example.com", passwort="Passwort123"):
        return speicher.benutzer_erstellen(benutzername, email, passwort)


# --- A/B: Registrierung + unverifizierter Ausgangszustand ---------------


class RegistrierungTests(_TempDbTestCase):
    def test_a_registrierung_legt_konto_an(self):
        benutzer_id = self._neuer_benutzer()
        konto = speicher.benutzer_nach_id(benutzer_id)
        self.assertIsNotNone(konto)
        self.assertEqual(konto["benutzername"], "testuser")
        self.assertEqual(konto["email"], "test@example.com")

    def test_b_neues_konto_ist_unverifiziert(self):
        benutzer_id = self._neuer_benutzer()
        konto = speicher.benutzer_nach_id(benutzer_id)
        self.assertEqual(konto["email_verified"], 0)

    def test_q_insert_ohne_email_verified_bleibt_verifiziert(self):
        """Simuliert einen Alt-Datensatz, der NICHT über `benutzer_erstellen`
        (welches email_verified=0 explizit setzt) angelegt wurde, sondern
        über das Tabellen-Default - z. B. das Migrations-/Bootstrap-Konto
        oder eine echte Alt-Zeile aus der Zeit vor dieser Änderung. Muss
        weiterhin sofort nutzbar (verifiziert) sein.
        """
        with speicher._verbindung() as conn:
            jetzt = speicher._jetzt()
            conn.execute(
                "INSERT INTO benutzer (benutzername, email, passwort_hash, erstellt_am, aktualisiert_am, aktiv) "
                "VALUES (?, ?, ?, ?, ?, 1)",
                ("altbestand_sim", "alt@example.com", auth.passwort_hash("Altpass123"), jetzt, jetzt),
            )

        konto = speicher.benutzer_nach_login("altbestand_sim")
        self.assertIsNotNone(konto)
        self.assertEqual(konto["email_verified"], 1)
        self.assertTrue(auth.passwort_pruefen("Altpass123", konto["passwort_hash"]))


# --- D/E/F: E-Mail-Verifizierung -----------------------------------------


class EmailVerifizierungTests(_TempDbTestCase):
    def test_d_gueltige_verifizierung(self):
        benutzer_id = self._neuer_benutzer()
        token = speicher.email_verifizierung_erstellen(benutzer_id, "test@example.com")

        erfolg, meldung, betroffene_id = speicher.email_verifizierung_bestaetigen(token)

        self.assertTrue(erfolg)
        self.assertEqual(betroffene_id, benutzer_id)
        self.assertEqual(speicher.benutzer_nach_id(benutzer_id)["email_verified"], 1)

    def test_e_abgelaufener_token(self):
        benutzer_id = self._neuer_benutzer()

        ursprung = speicher.EMAIL_VERIFIZIERUNG_GUELTIGKEIT_STUNDEN
        try:
            speicher.EMAIL_VERIFIZIERUNG_GUELTIGKEIT_STUNDEN = -1
            token = speicher.email_verifizierung_erstellen(benutzer_id, "test@example.com")
        finally:
            speicher.EMAIL_VERIFIZIERUNG_GUELTIGKEIT_STUNDEN = ursprung

        erfolg, meldung, _ = speicher.email_verifizierung_bestaetigen(token)

        self.assertFalse(erfolg)
        self.assertIn("abgelaufen", meldung)
        self.assertEqual(speicher.benutzer_nach_id(benutzer_id)["email_verified"], 0)

    def test_f_token_nur_einmal_verwendbar(self):
        benutzer_id = self._neuer_benutzer()
        token = speicher.email_verifizierung_erstellen(benutzer_id, "test@example.com")

        erster_versuch, _, _ = speicher.email_verifizierung_bestaetigen(token)
        zweiter_versuch, meldung, _ = speicher.email_verifizierung_bestaetigen(token)

        self.assertTrue(erster_versuch)
        self.assertFalse(zweiter_versuch)
        self.assertIn("ungültig", meldung.lower())

    def test_neuer_token_macht_alten_ungueltig(self):
        benutzer_id = self._neuer_benutzer()
        alter_token = speicher.email_verifizierung_erstellen(benutzer_id, "test@example.com")
        neuer_token = speicher.email_verifizierung_erstellen(benutzer_id, "test@example.com")

        erfolg_alt, _, _ = speicher.email_verifizierung_bestaetigen(alter_token)
        erfolg_neu, _, _ = speicher.email_verifizierung_bestaetigen(neuer_token)

        self.assertFalse(erfolg_alt)
        self.assertTrue(erfolg_neu)

    def test_token_wird_niemals_im_klartext_gespeichert(self):
        benutzer_id = self._neuer_benutzer()
        token = speicher.email_verifizierung_erstellen(benutzer_id, "test@example.com")

        with speicher._verbindung() as conn:
            zeilen = conn.execute("SELECT token_hash FROM email_verifications").fetchall()

        self.assertTrue(zeilen)
        for zeile in zeilen:
            self.assertNotEqual(zeile["token_hash"], token)


# --- G: Erneutes Senden / Cooldown ---------------------------------------


class ResendCooldownTests(_TempDbTestCase):
    def test_g_cooldown_aktiv_nach_versand(self):
        email = "test@example.com"
        aktiv_vorher, _ = ratenbegrenzung.resend_cooldown_aktiv(email)
        self.assertFalse(aktiv_vorher)

        ratenbegrenzung.versuch_aufzeichnen("resend_verification", email, True)

        aktiv_nachher, wartezeit = ratenbegrenzung.resend_cooldown_aktiv(email)
        self.assertTrue(aktiv_nachher)
        self.assertGreater(wartezeit, 0)

    def test_resend_limit_greift_nach_max_versuchen(self):
        email = "spam@example.com"

        for _ in range(3):
            erlaubt, _ = ratenbegrenzung.pruefen("resend_verification", email)
            self.assertTrue(erlaubt)
            ratenbegrenzung.versuch_aufzeichnen("resend_verification", email, True)

        erlaubt, wartezeit = ratenbegrenzung.pruefen("resend_verification", email)
        self.assertFalse(erlaubt)
        self.assertGreater(wartezeit, 0)


# --- H/I: Passwort vergessen (Anti-Enumeration) ---------------------------


class PasswortVergessenTests(_TempDbTestCase):
    def test_h_existierende_email_liefert_token(self):
        self._neuer_benutzer(email="vorhanden@example.com")
        token, konto_email = speicher.passwort_reset_anfordern("vorhanden@example.com")

        self.assertIsNotNone(token)
        self.assertEqual(konto_email, "vorhanden@example.com")

    def test_i_nicht_existierende_email_liefert_kein_token(self):
        token, konto_email = speicher.passwort_reset_anfordern("nichtvorhanden@example.com")

        self.assertIsNone(token)
        self.assertIsNone(konto_email)

    def test_i_ui_meldung_ist_immer_identisch(self):
        """Die eigentliche Anti-Enumeration-Garantie: `benutzer.py`s
        Formular-Handler gibt IMMER `PASSWORT_RESET_MELDUNG` zurück, egal
        ob `speicher.passwort_reset_anfordern` einen Token liefert oder
        nicht. `benutzer` wird hier bewusst erst lokal importiert - der
        Import selbst ist Streamlit-unabhängig (nur Modulkonstanten/
        Funktionsdefinitionen auf oberster Ebene), der Aufruf berührt
        aber `st.session_state` nicht.
        """
        import benutzer

        self._neuer_benutzer(email="vorhanden2@example.com")

        meldung_existiert = benutzer._passwort_reset_anfordern("vorhanden2@example.com")
        meldung_nicht_existiert = benutzer._passwort_reset_anfordern("nichtvorhanden2@example.com")

        self.assertEqual(meldung_existiert, benutzer.PASSWORT_RESET_MELDUNG)
        self.assertEqual(meldung_nicht_existiert, benutzer.PASSWORT_RESET_MELDUNG)
        self.assertEqual(meldung_existiert, meldung_nicht_existiert)


# --- J/K/L: Passwort-Reset-Ablauf -----------------------------------------


class PasswortResetTests(_TempDbTestCase):
    def test_j_gueltiger_reset_aendert_passwort(self):
        benutzer_id = self._neuer_benutzer(passwort="AltesPasswort1")
        token, _ = speicher.passwort_reset_anfordern("test@example.com")

        erfolg, meldung, betroffene_id = speicher.passwort_reset_einloesen(token, "NeuesPasswort1")

        self.assertTrue(erfolg)
        self.assertEqual(betroffene_id, benutzer_id)

        konto = speicher.benutzer_nach_login("testuser")
        self.assertTrue(auth.passwort_pruefen("NeuesPasswort1", konto["passwort_hash"]))
        self.assertFalse(auth.passwort_pruefen("AltesPasswort1", konto["passwort_hash"]))

    def test_k_token_nicht_zweimal_verwendbar(self):
        self._neuer_benutzer()
        token, _ = speicher.passwort_reset_anfordern("test@example.com")

        erster, _, _ = speicher.passwort_reset_einloesen(token, "NeuesPasswort1")
        zweiter, meldung, _ = speicher.passwort_reset_einloesen(token, "NochEinPasswort1")

        self.assertTrue(erster)
        self.assertFalse(zweiter)
        self.assertIn("ungültig", meldung.lower())

    def test_l_alte_sitzungen_nach_reset_ungueltig(self):
        benutzer_id = self._neuer_benutzer()
        sitzung_token = speicher.sitzung_erstellen(benutzer_id)
        self.assertEqual(speicher.sitzung_pruefen_und_aktualisieren(sitzung_token), benutzer_id)

        token, _ = speicher.passwort_reset_anfordern("test@example.com")
        speicher.passwort_reset_einloesen(token, "NeuesPasswort1")

        self.assertIsNone(speicher.sitzung_pruefen_und_aktualisieren(sitzung_token))

    def test_neuer_reset_macht_alten_ungueltig(self):
        self._neuer_benutzer()
        alter_token, _ = speicher.passwort_reset_anfordern("test@example.com")
        neuer_token, _ = speicher.passwort_reset_anfordern("test@example.com")

        erfolg_alt, _, _ = speicher.passwort_reset_einloesen(alter_token, "Irgendwas123")
        self.assertFalse(erfolg_alt)

        erfolg_neu, _, _ = speicher.passwort_reset_einloesen(neuer_token, "Irgendwas123")
        self.assertTrue(erfolg_neu)

    def test_abgelaufener_reset_token(self):
        self._neuer_benutzer()

        ursprung = speicher.PASSWORT_RESET_GUELTIGKEIT_STUNDEN
        try:
            speicher.PASSWORT_RESET_GUELTIGKEIT_STUNDEN = -1
            token, _ = speicher.passwort_reset_anfordern("test@example.com")
        finally:
            speicher.PASSWORT_RESET_GUELTIGKEIT_STUNDEN = ursprung

        erfolg, meldung, _ = speicher.passwort_reset_einloesen(token, "Irgendwas123")
        self.assertFalse(erfolg)
        self.assertIn("abgelaufen", meldung)


# --- M/N: Rate-Limiting ----------------------------------------------------


class RateLimitTests(_TempDbTestCase):
    def test_m_login_rate_limit_nach_fehlversuchen(self):
        identitaet = "opfer@example.com"

        for _ in range(5):
            erlaubt, _ = ratenbegrenzung.pruefen("login", identitaet)
            self.assertTrue(erlaubt)
            ratenbegrenzung.versuch_aufzeichnen("login", identitaet, False)

        erlaubt, wartezeit = ratenbegrenzung.pruefen("login", identitaet)
        self.assertFalse(erlaubt)
        self.assertGreater(wartezeit, 0)

    def test_login_erfolgreicher_versuch_zaehlt_nicht_gegen_limit(self):
        identitaet = "brav@example.com"

        for _ in range(10):
            ratenbegrenzung.versuch_aufzeichnen("login", identitaet, True)

        erlaubt, _ = ratenbegrenzung.pruefen("login", identitaet)
        self.assertTrue(erlaubt)

    def test_n_reset_rate_limit(self):
        identitaet = "reset-spam@example.com"

        for _ in range(5):
            erlaubt, _ = ratenbegrenzung.pruefen("password_reset_request", identitaet)
            self.assertTrue(erlaubt)
            ratenbegrenzung.versuch_aufzeichnen("password_reset_request", identitaet, True)

        erlaubt, wartezeit = ratenbegrenzung.pruefen("password_reset_request", identitaet)
        self.assertFalse(erlaubt)
        self.assertGreater(wartezeit, 0)

    def test_rate_limit_ist_identitaets_unabhaengig_von_kontoexistenz(self):
        """Kein unterschiedliches Verhalten für existierende vs. nicht
        existierende Konten - dieselbe Zähl-/Sperrlogik gilt für beide."""
        self._neuer_benutzer(email="existiert@example.com")

        for identitaet in ("existiert@example.com", "existiert-nicht@example.com"):
            for _ in range(5):
                erlaubt, _ = ratenbegrenzung.pruefen("login", identitaet)
                self.assertTrue(erlaubt)
                ratenbegrenzung.versuch_aufzeichnen("login", identitaet, False)

            erlaubt, wartezeit = ratenbegrenzung.pruefen("login", identitaet)
            self.assertFalse(erlaubt)
            self.assertGreater(wartezeit, 0)

    def test_rate_limit_eskaliert_bei_wiederholten_sperren(self):
        identitaet = "wiederholter-angreifer@example.com"

        # Erste Sperr-Episode auslösen.
        for _ in range(5):
            ratenbegrenzung.versuch_aufzeichnen("login", identitaet, False)

        _, erste_wartezeit = ratenbegrenzung.pruefen("login", identitaet)
        self.assertGreater(erste_wartezeit, 0)

        # Zeitpunkt des letzten Sperr-Ereignisses künstlich weit genug in
        # die Vergangenheit verschieben, damit ein erneuter Verstoß als
        # NEUE Sperr-Episode zählt (ohne auf echte Wartezeit zu warten).
        with speicher._verbindung() as conn:
            konn_zeit = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
            conn.execute(
                "UPDATE security_events SET ts = ? WHERE event_type = 'rate_limit_triggered' AND identitaet = ?",
                (konn_zeit, identitaet),
            )
            conn.execute(
                "UPDATE security_events SET ts = ? WHERE event_type = 'login_versuch' AND identitaet = ?",
                (konn_zeit, identitaet),
            )

        for _ in range(5):
            ratenbegrenzung.versuch_aufzeichnen("login", identitaet, False)

        _, zweite_wartezeit = ratenbegrenzung.pruefen("login", identitaet)
        self.assertGreater(zweite_wartezeit, erste_wartezeit)


# --- O: Logout invalidiert Sitzung -----------------------------------------


class SitzungsTests(_TempDbTestCase):
    def test_o_widerruf_invalidiert_sitzung(self):
        benutzer_id = self._neuer_benutzer()
        token = speicher.sitzung_erstellen(benutzer_id)

        self.assertEqual(speicher.sitzung_pruefen_und_aktualisieren(token), benutzer_id)

        speicher.sitzung_widerrufen(token)

        self.assertIsNone(speicher.sitzung_pruefen_und_aktualisieren(token))

    def test_sitzung_erstellen_erzeugt_immer_neuen_token(self):
        benutzer_id = self._neuer_benutzer()
        token1 = speicher.sitzung_erstellen(benutzer_id)
        token2 = speicher.sitzung_erstellen(benutzer_id)

        self.assertNotEqual(token1, token2)
        self.assertEqual(speicher.sitzung_pruefen_und_aktualisieren(token1), benutzer_id)
        self.assertEqual(speicher.sitzung_pruefen_und_aktualisieren(token2), benutzer_id)

    def test_sitzung_token_wird_nicht_im_klartext_gespeichert(self):
        benutzer_id = self._neuer_benutzer()
        token = speicher.sitzung_erstellen(benutzer_id)

        with speicher._verbindung() as conn:
            zeilen = conn.execute("SELECT token_hash FROM sessions").fetchall()

        for zeile in zeilen:
            self.assertNotEqual(zeile["token_hash"], token)

    def test_sitzung_abgelaufen_nach_max_lebensdauer(self):
        benutzer_id = self._neuer_benutzer()
        token = speicher.sitzung_erstellen(benutzer_id)

        with speicher._verbindung() as conn:
            abgelaufen = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
            conn.execute(
                "UPDATE sessions SET laeuft_ab_am = ? WHERE token_hash = ?",
                (abgelaufen, speicher._token_hash(token)),
            )

        self.assertIsNone(speicher.sitzung_pruefen_und_aktualisieren(token))

    def test_sitzung_abgelaufen_nach_inaktivitaet(self):
        benutzer_id = self._neuer_benutzer()
        token = speicher.sitzung_erstellen(benutzer_id)

        with speicher._verbindung() as conn:
            lange_inaktiv = (
                datetime.now() - timedelta(minutes=speicher.SITZUNG_INAKTIVITAET_MINUTEN + 5)
            ).isoformat(timespec="seconds")
            conn.execute(
                "UPDATE sessions SET last_activity_at = ? WHERE token_hash = ?",
                (lange_inaktiv, speicher._token_hash(token)),
            )

        self.assertIsNone(speicher.sitzung_pruefen_und_aktualisieren(token))

    def test_widerruf_mit_ausnahme_behaelt_eine_sitzung(self):
        benutzer_id = self._neuer_benutzer()
        behalten = speicher.sitzung_erstellen(benutzer_id)
        widerrufen = speicher.sitzung_erstellen(benutzer_id)

        speicher.sitzungen_widerrufen_fuer_benutzer(benutzer_id, ausser_roher_token=behalten)

        self.assertEqual(speicher.sitzung_pruefen_und_aktualisieren(behalten), benutzer_id)
        self.assertIsNone(speicher.sitzung_pruefen_und_aktualisieren(widerrufen))

    def test_widerruf_ohne_ausnahme_beendet_alle_sitzungen(self):
        benutzer_id = self._neuer_benutzer()
        token1 = speicher.sitzung_erstellen(benutzer_id)
        token2 = speicher.sitzung_erstellen(benutzer_id)

        speicher.sitzungen_widerrufen_fuer_benutzer(benutzer_id)

        self.assertIsNone(speicher.sitzung_pruefen_und_aktualisieren(token1))
        self.assertIsNone(speicher.sitzung_pruefen_und_aktualisieren(token2))


# --- P: IDOR / Datentrennung zwischen Benutzern -----------------------------


class DatentrennungTests(_TempDbTestCase):
    def setUp(self):
        super().setUp()
        self.user_a = self._neuer_benutzer("user_a", "a@example.com", "PasswortA1")
        self.user_b = self._neuer_benutzer("user_b", "b@example.com", "PasswortB1")

        self.dokument_a = speicher.dokument_speichern(
            "geheim.pdf", "hash-a", b"Inhalt A", 1, "pdf", "seite", self.user_a
        )
        self.chat_a = speicher.chat_erstellen(self.user_a)
        speicher.nachricht_hinzufuegen(self.chat_a, self.user_a, "Frage A", "Antwort A", [])

    def test_p_dokumente_nicht_ueber_fremden_benutzer_sichtbar(self):
        self.assertEqual(speicher.dokumente_laden(self.user_b), [])
        self.assertEqual(len(speicher.dokumente_laden(self.user_a)), 1)

    def test_p_chat_nicht_ueber_fremden_benutzer_ladbar(self):
        self.assertIsNone(speicher.chat_laden(self.chat_a, self.user_b))
        self.assertIsNotNone(speicher.chat_laden(self.chat_a, self.user_a))

    def test_p_chunks_nicht_ueber_fremden_benutzer_ladbar(self):
        self.assertEqual(speicher.chunks_laden([self.dokument_a], self.user_b), [])

    def test_p_dokument_datei_nicht_ueber_fremden_benutzer_lesbar(self):
        self.assertIsNone(speicher.dokument_datei_lesen(self.dokument_a, self.user_b))

    def test_p_fremdes_dokument_kann_nicht_geloescht_werden(self):
        speicher.dokument_loeschen(self.dokument_a, self.user_b)
        self.assertEqual(len(speicher.dokumente_laden(self.user_a)), 1)

    def test_p_nachricht_kann_nicht_in_fremden_chat_geschrieben_werden(self):
        with self.assertRaises(PermissionError):
            speicher.nachricht_hinzufuegen(self.chat_a, self.user_b, "Eingeschleust", "X", [])

    def test_p_fremde_dokument_id_wird_aus_chat_auswahl_gefiltert(self):
        chat_b = speicher.chat_erstellen(self.user_b)
        speicher.chat_dokumente_setzen(chat_b, [self.dokument_a], self.user_b)
        geladen = speicher.chat_laden(chat_b, self.user_b)
        self.assertEqual(geladen["dokument_ids"], [])


# --- R: Migration ist idempotent --------------------------------------------


class MigrationTests(_TempDbTestCase):
    def test_r_datenbank_initialisierung_zweimal_ohne_fehler(self):
        benutzer_id = self._neuer_benutzer()
        speicher.datenbank_initialisieren()
        speicher.datenbank_initialisieren()

        konto = speicher.benutzer_nach_id(benutzer_id)
        self.assertIsNotNone(konto)
        self.assertEqual(konto["benutzername"], "testuser")


# --- E-Mail-Versand: Dev-/Resend-Anbieter -----------------------------------


class EmailVersandTests(unittest.TestCase):
    def setUp(self):
        email_versand.GESENDETE_ENTWICKLUNGS_MAILS.clear()

    def test_dev_anbieter_versendet_keine_echte_mail(self):
        ergebnis = email_versand.versenden("empfaenger@example.com", "Betreff", "Text")
        self.assertEqual(ergebnis["empfaenger"], "empfaenger@example.com")
        self.assertEqual(len(email_versand.GESENDETE_ENTWICKLUNGS_MAILS), 1)

    def test_resend_ohne_api_key_wirft_klaren_fehler_ohne_leak(self):
        os.environ["CLEVORIQ_EMAIL_PROVIDER"] = "resend"
        os.environ.pop("RESEND_API_KEY", None)
        os.environ.pop("CLEVORIQ_EMAIL_FROM", None)
        try:
            with self.assertRaises(RuntimeError) as kontext:
                email_versand.versenden("x@example.com", "Betreff", "Text")
            meldung = str(kontext.exception)
            self.assertIn("RESEND_API_KEY", meldung)
            self.assertNotIn("Bearer", meldung)
        finally:
            os.environ["CLEVORIQ_EMAIL_PROVIDER"] = "dev"

    def test_verifizierungs_link_enthaelt_token(self):
        link = email_versand.verifizierungs_link("abc123")
        self.assertIn("verify_token=abc123", link)

    def test_reset_link_enthaelt_token(self):
        link = email_versand.reset_link("xyz789")
        self.assertIn("reset_token=xyz789", link)


if __name__ == "__main__":
    unittest.main(verbosity=2)
