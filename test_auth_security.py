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
was in der Shell-Umgebung sonst gesetzt ist. Setzt außerdem einen FEST
DEFINIERTEN, NUR FÜR TESTS bestimmten `CLEVORIQ_2FA_ENCRYPTION_KEY`
(`_TEST_2FA_SCHLUESSEL` unten - ein via `Fernet.generate_key()` erzeugter
Platzhalter, kein irgendwo produktiv verwendetes Geheimnis) - ohne einen
gültigen Schlüssel würde jede 2FA-Funktion, die ein Secret ver-/
entschlüsselt, absichtlich mit einem `RuntimeError` fehlschlagen (siehe
`zwei_faktor_krypto.py`). Jeder Test arbeitet auf einer frischen,
temporären SQLite-Datenbank (kein Zugriff auf `app_daten/` des echten
Projekts) und macht KEINE echten OpenAI- oder Resend-Aufrufe.
"""

import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

os.environ["CLEVORIQ_EMAIL_PROVIDER"] = "dev"
os.environ.pop("AVENLOQ_EMAIL_PROVIDER", None)
os.environ.pop("RESEND_API_KEY", None)

# NUR für automatisierte Tests - ein via `Fernet.generate_key()` einmalig
# erzeugter Platzhalterschlüssel, niemals in Produktion verwendet oder
# irgendwo sonst referenziert. Siehe `zwei_faktor_krypto.py`s Docstring:
# ohne gültigen `CLEVORIQ_2FA_ENCRYPTION_KEY` schlägt jede 2FA-Ver-/
# Entschlüsselung sicher fehl statt auf einen Klartext-Fallback auszuweichen.
_TEST_2FA_SCHLUESSEL = "yq3nD5wq0v1sO4kQe9ZfW2mC7bH8jU6xR1tL0nA5pY4="
os.environ["CLEVORIQ_2FA_ENCRYPTION_KEY"] = _TEST_2FA_SCHLUESSEL
os.environ.pop("CLEVORIQ_2FA_ENCRYPTION_KEY_V2", None)

import auth  # noqa: E402
import email_versand  # noqa: E402
import pyotp  # noqa: E402
import ratenbegrenzung  # noqa: E402
import speicher  # noqa: E402
import zwei_faktor_krypto  # noqa: E402


def _naechster_totp_code(secret):
    """Erzeugt einen TOTP-Code für den NÄCHSTEN Zeitschritt (jetzt + 30s).

    `_2fa_aktivieren` verbraucht (korrekt, siehe Replay-Schutz) bereits
    den Zeitschritt, in dem die Aktivierung stattfand - ein Test, der
    Millisekunden später erneut `pyotp.TOTP(secret).now()` aufruft, würde
    mit hoher Wahrscheinlichkeit denselben Zeitschritt treffen und vom
    Replay-Schutz (korrekterweise!) abgelehnt werden. Diese Hilfsfunktion
    erzeugt deterministisch einen Code für den EXAKT nächsten Zeitschritt
    (Differenz von genau `TOTP_SCHRITT_SEKUNDEN`, unabhängig davon, wo
    `jetzt` gerade innerhalb seines eigenen Zeitschritts liegt) und bleibt
    dabei innerhalb des ±1-Toleranzfensters der eigentlichen Prüfung.
    """
    return pyotp.TOTP(secret).at(time.time() + zwei_faktor_krypto.TOTP_SCHRITT_SEKUNDEN)


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

    def _2fa_aktivieren(self, benutzer_id):
        """Durchläuft den vollständigen 2FA-Einrichtungsablauf (Setup
        starten, mit einem echten, aktuell gültigen TOTP-Code bestätigen)
        und gibt `(secret, backup_codes_klartext)` zurück - Hilfsfunktion
        für Tests, die einen bereits aktivierten Zustand voraussetzen."""
        secret, _uri = speicher.zwei_faktor_setup_starten(benutzer_id)
        code = pyotp.TOTP(secret).now()
        erfolg, meldung, backup_codes = speicher.zwei_faktor_setup_bestaetigen(benutzer_id, code)
        assert erfolg, meldung
        return secret, backup_codes


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


# --- Zwei-Faktor-Authentifizierung (TOTP) -----------------------------------
#
# Deckt die Buchstaben A-Z aus dem 2FA-Arbeitsblock ab, soweit an der
# Streamlit-unabhängigen `speicher.py`/`zwei_faktor_krypto.py`-Schicht
# sinnvoll testbar (siehe Moduldocstring). UI-getriebene Abläufe, die
# zwingend mehrere Streamlit-Formulare in Reihenfolge durchlaufen (2FA
# OHNE Passwort/ohne zweiten Faktor deaktivieren, Konto mit 2FA löschen),
# werden zusätzlich per `streamlit.testing.v1.AppTest` geprüft (siehe
# separates Live-UI-Testprotokoll im Abschlussbericht) - hier wird die
# zugrunde liegende Invariante ("falsches Passwort/falscher Code lässt
# die Aktion serverseitig nicht zu") direkt an der Datenschicht geprüft.


class ZweiFaktorSetupTests(_TempDbTestCase):
    def test_2fa_a_ohne_2fa_kein_zwang(self):
        benutzer_id = self._neuer_benutzer()
        status = speicher.zwei_faktor_status(benutzer_id)
        self.assertFalse(status["aktiv"])

    def test_2fa_b_setup_erstellt_pending(self):
        benutzer_id = self._neuer_benutzer()
        secret, uri = speicher.zwei_faktor_setup_starten(benutzer_id)

        self.assertTrue(secret)
        self.assertIn("otpauth://totp/", uri)
        self.assertIn("Clevoriq", uri)

        status = speicher.zwei_faktor_status(benutzer_id)
        self.assertFalse(status["aktiv"])
        self.assertTrue(status["pending"])

    def test_2fa_c_falscher_setup_code_aktiviert_nicht(self):
        benutzer_id = self._neuer_benutzer()
        speicher.zwei_faktor_setup_starten(benutzer_id)

        erfolg, meldung, backup_codes = speicher.zwei_faktor_setup_bestaetigen(benutzer_id, "000000")

        self.assertFalse(erfolg)
        self.assertIsNone(backup_codes)
        self.assertFalse(speicher.zwei_faktor_status(benutzer_id)["aktiv"])

    def test_2fa_d_korrekter_setup_code_aktiviert(self):
        benutzer_id = self._neuer_benutzer()
        secret, backup_codes = self._2fa_aktivieren(benutzer_id)

        status = speicher.zwei_faktor_status(benutzer_id)
        self.assertTrue(status["aktiv"])
        self.assertFalse(status["pending"])
        self.assertEqual(len(backup_codes), speicher.BACKUP_CODES_ANZAHL)

    def test_2fa_e_secret_nicht_im_klartext_in_db(self):
        benutzer_id = self._neuer_benutzer()
        secret, _ = self._2fa_aktivieren(benutzer_id)

        with speicher._verbindung() as conn:
            zeile = conn.execute(
                "SELECT secret_verschluesselt FROM zwei_faktor WHERE user_id = ?", (benutzer_id,)
            ).fetchone()

        self.assertIsNotNone(zeile["secret_verschluesselt"])
        self.assertNotEqual(zeile["secret_verschluesselt"], secret)
        self.assertNotIn(secret, zeile["secret_verschluesselt"])

    def test_2fa_f_backup_codes_nur_gehasht_in_db(self):
        benutzer_id = self._neuer_benutzer()
        _secret, backup_codes = self._2fa_aktivieren(benutzer_id)

        with speicher._verbindung() as conn:
            zeilen = conn.execute(
                "SELECT code_hash FROM backup_codes WHERE user_id = ?", (benutzer_id,)
            ).fetchall()

        gespeicherte_hashes = {z["code_hash"] for z in zeilen}
        self.assertEqual(len(gespeicherte_hashes), len(backup_codes))

        for klartext_code in backup_codes:
            self.assertNotIn(klartext_code, gespeicherte_hashes)
            normalisiert = zwei_faktor_krypto.backup_code_normalisieren(klartext_code)
            # Argon2id ist gesalzen - jeder Klartext-Code muss gegen GENAU
            # einen der gespeicherten Hashes verifizieren, kein direkter
            # String-Vergleich (wie beim alten, ungesalzenen SHA-256) mehr
            # möglich.
            treffer = [h for h in gespeicherte_hashes if auth.backup_code_pruefen(normalisiert, h)]
            self.assertEqual(len(treffer), 1)

    def test_2fa_f2_backup_codes_kein_klartext_sha256_hash(self):
        """Stellt sicher, dass Backup-Codes NICHT mehr als einfacher,
        ungesalzener SHA-256-Hash (die alte, zu schwache Implementierung)
        gespeichert werden, sondern als gesalzener Argon2id-Hash (erkennbar
        am `$argon2id$`-Präfix des Standard-PHC-Encodings)."""
        benutzer_id = self._neuer_benutzer()
        _secret, backup_codes = self._2fa_aktivieren(benutzer_id)

        with speicher._verbindung() as conn:
            zeilen = conn.execute(
                "SELECT code_hash FROM backup_codes WHERE user_id = ?", (benutzer_id,)
            ).fetchall()

        for zeile in zeilen:
            code_hash = zeile["code_hash"]
            self.assertTrue(code_hash.startswith("$argon2id$"))

        for klartext_code in backup_codes:
            normalisiert = zwei_faktor_krypto.backup_code_normalisieren(klartext_code)
            alter_sha256_hash = speicher._token_hash(normalisiert)
            self.assertNotIn(alter_sha256_hash, {z["code_hash"] for z in zeilen})

    def test_2fa_pending_secret_laesst_aktives_secret_unangetastet(self):
        """Eine Neu-Einrichtung (Rotation) darf das noch aktive Secret
        NICHT vorzeitig zerstören, bevor der neue Code bestätigt wurde."""
        benutzer_id = self._neuer_benutzer()
        altes_secret, _ = self._2fa_aktivieren(benutzer_id)

        neues_secret, _uri = speicher.zwei_faktor_setup_starten(benutzer_id)
        self.assertNotEqual(altes_secret, neues_secret)

        # Das ALTE Secret muss weiterhin funktionieren, solange die neue
        # Einrichtung nicht bestätigt wurde.
        alter_code = _naechster_totp_code(altes_secret)
        gueltig, _meldung = speicher.zwei_faktor_totp_pruefen(benutzer_id, alter_code)
        self.assertTrue(gueltig)


class ZweiFaktorLoginChallengeTests(_TempDbTestCase):
    def setUp(self):
        super().setUp()
        self.benutzer_id = self._neuer_benutzer()
        self.secret, self.backup_codes = self._2fa_aktivieren(self.benutzer_id)

    def test_2fa_g_challenge_erstellt_keine_sitzung(self):
        """Simuliert den Zustand direkt nach korrektem Passwort, vor
        Abschluss der 2FA-Prüfung: es darf noch KEINE Sitzung existieren."""
        speicher.zwei_faktor_challenge_erstellen(self.benutzer_id)

        with speicher._verbindung() as conn:
            anzahl = conn.execute(
                "SELECT COUNT(*) AS n FROM sessions WHERE user_id = ?", (self.benutzer_id,)
            ).fetchone()["n"]

        self.assertEqual(anzahl, 0)

    def test_2fa_h_korrekter_totp_schliesst_challenge_ab(self):
        token = speicher.zwei_faktor_challenge_erstellen(self.benutzer_id)
        code = _naechster_totp_code(self.secret)

        erfolg, meldung, benutzer_id, beendet = speicher.zwei_faktor_challenge_pruefen_und_verbrauchen(
            token, code, False
        )

        self.assertTrue(erfolg)
        self.assertTrue(beendet)
        self.assertEqual(benutzer_id, self.benutzer_id)

        # Danach kann (wie es benutzer.py tut) eine echte Sitzung erstellt werden.
        sitzung_token = speicher.sitzung_erstellen(benutzer_id)
        self.assertEqual(speicher.sitzung_pruefen_und_aktualisieren(sitzung_token), benutzer_id)

    def test_2fa_i_falscher_totp_schliesst_nicht_ab(self):
        token = speicher.zwei_faktor_challenge_erstellen(self.benutzer_id)

        erfolg, meldung, benutzer_id, beendet = speicher.zwei_faktor_challenge_pruefen_und_verbrauchen(
            token, "000000", False
        )

        self.assertFalse(erfolg)
        self.assertFalse(beendet)  # erster Fehlversuch - Challenge lebt noch

    def test_2fa_j_abgelaufene_challenge_nicht_verwendbar(self):
        token = speicher.zwei_faktor_challenge_erstellen(self.benutzer_id)

        with speicher._verbindung() as conn:
            abgelaufen = (datetime.now() - timedelta(minutes=1)).isoformat(timespec="seconds")
            conn.execute(
                "UPDATE zwei_faktor_challenges SET laeuft_ab_am = ? WHERE challenge_token_hash = ?",
                (abgelaufen, speicher._token_hash(token)),
            )

        code = pyotp.TOTP(self.secret).now()
        erfolg, meldung, _benutzer_id, beendet = speicher.zwei_faktor_challenge_pruefen_und_verbrauchen(
            token, code, False
        )

        self.assertFalse(erfolg)
        self.assertTrue(beendet)
        self.assertIn("abgelaufen", meldung)

    def test_2fa_k_challenge_nur_einmal_verwendbar(self):
        token = speicher.zwei_faktor_challenge_erstellen(self.benutzer_id)
        code = _naechster_totp_code(self.secret)

        erster, _, _, _ = speicher.zwei_faktor_challenge_pruefen_und_verbrauchen(token, code, False)
        zweiter, meldung, _, beendet = speicher.zwei_faktor_challenge_pruefen_und_verbrauchen(
            token, code, False
        )

        self.assertTrue(erster)
        self.assertFalse(zweiter)
        self.assertTrue(beendet)

    def test_2fa_l_rate_limiting_bei_falschen_codes(self):
        identitaet = f"user:{self.benutzer_id}"

        for _ in range(5):
            erlaubt, _ = ratenbegrenzung.pruefen("totp_verify", identitaet)
            self.assertTrue(erlaubt)
            ratenbegrenzung.versuch_aufzeichnen("totp_verify", identitaet, False)

        erlaubt, wartezeit = ratenbegrenzung.pruefen("totp_verify", identitaet)
        self.assertFalse(erlaubt)
        self.assertGreater(wartezeit, 0)

    def test_2fa_m_gueltiger_backup_code_funktioniert(self):
        token = speicher.zwei_faktor_challenge_erstellen(self.benutzer_id)

        erfolg, _meldung, benutzer_id, beendet = speicher.zwei_faktor_challenge_pruefen_und_verbrauchen(
            token, self.backup_codes[0], True
        )

        self.assertTrue(erfolg)
        self.assertTrue(beendet)
        self.assertEqual(benutzer_id, self.benutzer_id)

    def test_2fa_n_backup_code_nur_einmal_verwendbar(self):
        code = self.backup_codes[0]

        erster = speicher.zwei_faktor_backup_code_pruefen_und_verbrauchen(self.benutzer_id, code)
        zweiter = speicher.zwei_faktor_backup_code_pruefen_und_verbrauchen(self.benutzer_id, code)

        self.assertTrue(erster)
        self.assertFalse(zweiter)

    def test_challenge_beendet_sich_nach_max_fehlversuchen(self):
        token = speicher.zwei_faktor_challenge_erstellen(self.benutzer_id)

        for i in range(speicher.ZWEI_FAKTOR_CHALLENGE_MAX_FEHLVERSUCHE):
            erfolg, _meldung, _uid, beendet = speicher.zwei_faktor_challenge_pruefen_und_verbrauchen(
                token, "000000", False
            )
            self.assertFalse(erfolg)
            erwartet_beendet = i == speicher.ZWEI_FAKTOR_CHALLENGE_MAX_FEHLVERSUCHE - 1
            self.assertEqual(beendet, erwartet_beendet)

        # Ein neuer Versuch mit demselben Token - selbst mit korrektem
        # Code - funktioniert danach nicht mehr (Challenge tot).
        code = pyotp.TOTP(self.secret).now()
        erfolg, _meldung, _uid, _beendet = speicher.zwei_faktor_challenge_pruefen_und_verbrauchen(
            token, code, False
        )
        self.assertFalse(erfolg)


class PasswortResetUnd2faTests(_TempDbTestCase):
    def test_2fa_o_passwort_reset_deaktiviert_2fa_nicht(self):
        benutzer_id = self._neuer_benutzer()
        self._2fa_aktivieren(benutzer_id)

        token, _konto_email = speicher.passwort_reset_anfordern("test@example.com")
        erfolg, _meldung, _uid = speicher.passwort_reset_einloesen(token, "NeuesPasswort1")

        self.assertTrue(erfolg)
        self.assertTrue(speicher.zwei_faktor_status(benutzer_id)["aktiv"])

    def test_2fa_o_naechster_login_braucht_neues_passwort_und_2fa(self):
        benutzer_id = self._neuer_benutzer()
        secret, _codes = self._2fa_aktivieren(benutzer_id)

        token, _ = speicher.passwort_reset_anfordern("test@example.com")
        speicher.passwort_reset_einloesen(token, "NeuesPasswort1")

        konto = speicher.benutzer_nach_login("testuser")
        self.assertFalse(auth.passwort_pruefen("Passwort123", konto["passwort_hash"]))
        self.assertTrue(auth.passwort_pruefen("NeuesPasswort1", konto["passwort_hash"]))
        self.assertTrue(speicher.zwei_faktor_status(benutzer_id)["aktiv"])

    def test_2fa_p_passwortaenderung_invalidiert_andere_sitzungen(self):
        benutzer_id = self._neuer_benutzer()
        self._2fa_aktivieren(benutzer_id)

        aktuelle_sitzung = speicher.sitzung_erstellen(benutzer_id)
        andere_sitzung = speicher.sitzung_erstellen(benutzer_id)

        speicher.passwort_aendern(benutzer_id, "Passwort123", "NeuesPasswort1")
        # Wie in konto.py: andere Sitzungen widerrufen, eigene ausgenommen.
        speicher.sitzungen_widerrufen_fuer_benutzer(benutzer_id, ausser_roher_token=aktuelle_sitzung)

        self.assertEqual(speicher.sitzung_pruefen_und_aktualisieren(aktuelle_sitzung), benutzer_id)
        self.assertIsNone(speicher.sitzung_pruefen_und_aktualisieren(andere_sitzung))
        # 2FA bleibt unberührt von einer reinen Passwortänderung.
        self.assertTrue(speicher.zwei_faktor_status(benutzer_id)["aktiv"])


class ZweiFaktorVerwaltungTests(_TempDbTestCase):
    def test_2fa_q_deaktivierung_serverseitig_an_passwort_gebunden(self):
        """Beweist die serverseitige Grundlage des in konto.py umgesetzten
        Gates (Passwort wird VOR jedem Aufruf von `zwei_faktor_deaktivieren`
        geprüft, siehe `konto._2fa_deaktivieren_ansicht`): ein falsches
        Passwort wird von `konto_passwort_gueltig` zuverlässig abgelehnt."""
        benutzer_id = self._neuer_benutzer()
        self._2fa_aktivieren(benutzer_id)

        self.assertFalse(speicher.konto_passwort_gueltig(benutzer_id, "FalschesPasswort1"))
        self.assertTrue(speicher.zwei_faktor_status(benutzer_id)["aktiv"])

    def test_2fa_r_deaktivierung_serverseitig_an_zweiten_faktor_gebunden(self):
        """Beweist die serverseitige Grundlage des zweiten Teils desselben
        Gates: ein falscher TOTP-/Backup-Code wird von
        `zwei_faktor_code_pruefen` zuverlässig abgelehnt, unabhängig vom
        (bereits separat geprüften) Passwort."""
        benutzer_id = self._neuer_benutzer()
        self._2fa_aktivieren(benutzer_id)

        gueltig, _meldung = speicher.zwei_faktor_code_pruefen(benutzer_id, "000000", False)
        self.assertFalse(gueltig)
        self.assertTrue(speicher.zwei_faktor_status(benutzer_id)["aktiv"])

    def test_2fa_s_korrekte_deaktivierung_entfernt_secret_und_codes(self):
        benutzer_id = self._neuer_benutzer()
        self._2fa_aktivieren(benutzer_id)

        speicher.zwei_faktor_deaktivieren(benutzer_id)

        status = speicher.zwei_faktor_status(benutzer_id)
        self.assertFalse(status["aktiv"])
        self.assertFalse(status["pending"])
        self.assertEqual(speicher.zwei_faktor_backup_codes_anzahl_uebrig(benutzer_id), 0)

        with speicher._verbindung() as conn:
            zeile = conn.execute(
                "SELECT secret_verschluesselt FROM zwei_faktor WHERE user_id = ?", (benutzer_id,)
            ).fetchone()
        self.assertIsNone(zeile["secret_verschluesselt"])

    def test_2fa_t_neue_backup_codes_invalidieren_alte(self):
        benutzer_id = self._neuer_benutzer()
        _secret, alte_codes = self._2fa_aktivieren(benutzer_id)

        neue_codes = speicher.zwei_faktor_backup_codes_neu_erzeugen(benutzer_id)

        self.assertNotEqual(set(alte_codes), set(neue_codes))
        self.assertFalse(
            speicher.zwei_faktor_backup_code_pruefen_und_verbrauchen(benutzer_id, alte_codes[0])
        )
        self.assertTrue(
            speicher.zwei_faktor_backup_code_pruefen_und_verbrauchen(benutzer_id, neue_codes[0])
        )

    def test_2fa_u_kontoloeschung_entfernt_2fa_daten(self):
        benutzer_id = self._neuer_benutzer()
        self._2fa_aktivieren(benutzer_id)

        speicher.konto_endgueltig_loeschen(benutzer_id)

        self.assertFalse(speicher.zwei_faktor_status(benutzer_id)["aktiv"])

        with speicher._verbindung() as conn:
            zwei_faktor_zeilen = conn.execute(
                "SELECT COUNT(*) AS n FROM zwei_faktor WHERE user_id = ?", (benutzer_id,)
            ).fetchone()["n"]
            backup_code_zeilen = conn.execute(
                "SELECT COUNT(*) AS n FROM backup_codes WHERE user_id = ?", (benutzer_id,)
            ).fetchone()["n"]

        self.assertEqual(zwei_faktor_zeilen, 0)
        self.assertEqual(backup_code_zeilen, 0)

    def test_2fa_v_fremder_benutzer_kann_2fa_daten_nicht_verwenden(self):
        user_a = self._neuer_benutzer("user_a", "a@example.com", "PasswortA1")
        user_b = self._neuer_benutzer("user_b", "b@example.com", "PasswortB1")
        secret_a, backup_codes_a = self._2fa_aktivieren(user_a)

        # B hat kein aktives 2FA - A's TOTP-Code gegen B geprüft schlägt fehl.
        code_a = pyotp.TOTP(secret_a).now()
        gueltig, meldung = speicher.zwei_faktor_totp_pruefen(user_b, code_a)
        self.assertFalse(gueltig)
        self.assertIn("nicht aktiv", meldung)

        # A's Backup-Code gegen B geprüft schlägt ebenfalls fehl.
        self.assertFalse(
            speicher.zwei_faktor_backup_code_pruefen_und_verbrauchen(user_b, backup_codes_a[0])
        )
        # ... und wurde dadurch NICHT verbraucht - A kann ihn noch nutzen.
        self.assertTrue(
            speicher.zwei_faktor_backup_code_pruefen_und_verbrauchen(user_a, backup_codes_a[0])
        )

        # B (ohne eigenes 2FA) kann A's Konto nicht deaktivieren, indem B
        # seine eigene `user_id` übergibt - es wirkt nur auf B's (nicht
        # vorhandenen) Status, A bleibt aktiv.
        speicher.zwei_faktor_deaktivieren(user_b)
        self.assertTrue(speicher.zwei_faktor_status(user_a)["aktiv"])

    def test_2fa_v_challenge_eines_benutzers_nicht_fuer_anderen_einloesbar(self):
        user_a = self._neuer_benutzer("user_a", "a@example.com", "PasswortA1")
        user_b = self._neuer_benutzer("user_b", "b@example.com", "PasswortB1")
        secret_a, _codes_a = self._2fa_aktivieren(user_a)
        secret_b, _codes_b = self._2fa_aktivieren(user_b)

        token_a = speicher.zwei_faktor_challenge_erstellen(user_a)
        code_b = pyotp.TOTP(secret_b).now()

        # B's eigener, gültiger Code kann A's Challenge nicht einlösen -
        # die Challenge prüft immer gegen IHREN EIGENEN user_id (A).
        erfolg, _meldung, benutzer_id, _beendet = speicher.zwei_faktor_challenge_pruefen_und_verbrauchen(
            token_a, code_b, False
        )
        self.assertFalse(erfolg)
        self.assertEqual(benutzer_id, user_a)

    def test_2fa_w_migration_zweimal_ohne_fehler(self):
        benutzer_id = self._neuer_benutzer()
        self._2fa_aktivieren(benutzer_id)

        speicher.datenbank_initialisieren()
        speicher.datenbank_initialisieren()

        status = speicher.zwei_faktor_status(benutzer_id)
        self.assertTrue(status["aktiv"])

    def test_2fa_x_fehlender_schluessel_scheitert_sicher(self):
        gesichert = os.environ.pop("CLEVORIQ_2FA_ENCRYPTION_KEY", None)
        try:
            with self.assertRaises(RuntimeError) as kontext:
                zwei_faktor_krypto.secret_verschluesseln("EINGESETZTESSECRETXYZ")
            self.assertIn("CLEVORIQ_2FA_ENCRYPTION_KEY", str(kontext.exception))
        finally:
            if gesichert is not None:
                os.environ["CLEVORIQ_2FA_ENCRYPTION_KEY"] = gesichert

    def test_2fa_x_ungueltiger_schluessel_scheitert_sicher(self):
        gesichert = os.environ.get("CLEVORIQ_2FA_ENCRYPTION_KEY")
        os.environ["CLEVORIQ_2FA_ENCRYPTION_KEY"] = "kein-gueltiger-fernet-schluessel"
        try:
            with self.assertRaises(RuntimeError):
                zwei_faktor_krypto.secret_verschluesseln("EINGESETZTESSECRETXYZ")
        finally:
            os.environ["CLEVORIQ_2FA_ENCRYPTION_KEY"] = gesichert

    def test_2fa_x_verifizierung_scheitert_sicher_ohne_klartext_fallback(self):
        """Aktiviert 2FA MIT gültigem Schlüssel, entfernt ihn dann VOR der
        eigentlichen Code-Prüfung - die Prüfung muss (fail-closed) `False`
        mit einer TECHNISCHEN Meldung liefern, NIEMALS den Code stillschweigend
        als gültig akzeptieren."""
        benutzer_id = self._neuer_benutzer()
        secret, _codes = self._2fa_aktivieren(benutzer_id)
        code = pyotp.TOTP(secret).now()

        gesichert = os.environ.pop("CLEVORIQ_2FA_ENCRYPTION_KEY", None)
        try:
            gueltig, meldung = speicher.zwei_faktor_totp_pruefen(benutzer_id, code)
            self.assertFalse(gueltig)
            self.assertTrue(meldung)
        finally:
            if gesichert is not None:
                os.environ["CLEVORIQ_2FA_ENCRYPTION_KEY"] = gesichert

    def test_2fa_y_secrets_und_codes_nicht_in_security_log(self):
        benutzer_id = self._neuer_benutzer()
        secret, backup_codes = self._2fa_aktivieren(benutzer_id)

        token = speicher.zwei_faktor_challenge_erstellen(benutzer_id)
        code = pyotp.TOTP(secret).now()
        speicher.zwei_faktor_challenge_pruefen_und_verbrauchen(token, code, False)
        speicher.zwei_faktor_backup_code_pruefen_und_verbrauchen(benutzer_id, backup_codes[1])

        with speicher._verbindung() as conn:
            zeilen = conn.execute(
                "SELECT identitaet, detail FROM security_events"
            ).fetchall()

        verbotene_werte = [secret, code] + backup_codes
        for zeile in zeilen:
            for feld in ("identitaet", "detail"):
                wert = zeile[feld]
                if not wert:
                    continue
                for verboten in verbotene_werte:
                    self.assertNotIn(verboten, wert)

    def test_2fa_z_replay_schutz_verhindert_doppelte_verwendung(self):
        benutzer_id = self._neuer_benutzer()
        secret, _codes = self._2fa_aktivieren(benutzer_id)
        code = _naechster_totp_code(secret)

        erster_gueltig, _meldung = speicher.zwei_faktor_totp_pruefen(benutzer_id, code)
        zweiter_gueltig, _meldung = speicher.zwei_faktor_totp_pruefen(benutzer_id, code)

        self.assertTrue(erster_gueltig)
        self.assertFalse(zweiter_gueltig)

    def test_2fa_z_replay_schutz_erlaubt_spaeteren_neuen_zeitschritt(self):
        secret = zwei_faktor_krypto.neues_totp_secret()
        basis_zeit = time.time()

        gueltig1, schritt1 = zwei_faktor_krypto.totp_code_pruefen(
            secret, pyotp.TOTP(secret).at(basis_zeit), jetzt=basis_zeit
        )
        self.assertTrue(gueltig1)

        spaetere_zeit = basis_zeit + zwei_faktor_krypto.TOTP_SCHRITT_SEKUNDEN
        gueltig2, schritt2 = zwei_faktor_krypto.totp_code_pruefen(
            secret,
            pyotp.TOTP(secret).at(spaetere_zeit),
            letzter_zeitschritt=schritt1,
            jetzt=spaetere_zeit,
        )
        self.assertTrue(gueltig2)
        self.assertGreater(schritt2, schritt1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
