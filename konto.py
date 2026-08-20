"""Streamlit-Oberfläche für den Bereich "⚙️ Konto & Sicherheit".

Bündelt Profilbearbeitung, E-Mail-Bestätigung, Passwortänderung,
Datenexport und Kontolöschung an einem Ort. Jede Funktion hier erhält
ausschließlich den `benutzer_id` der angemeldeten Sitzung (von
`web_app.py` durchgereicht, nie ein aus einem Formularfeld gelesener
Wert) und delegiert jede eigentliche Prüfung/Änderung an `speicher.py` -
dieselbe Schichtentrennung wie im Rest der App (siehe `speicher.py`s
Prinzip der strikten Datentrennung). Wiederverwendet bewusst dieselben
Clevoriq-Bausteine wie die übrigen Bereiche (`komponenten.seiten_kopf`,
`st.form_submit_button(type="primary")`, `komponenten.hinweis_dezent`,
`st.container(border=True)`) - keine eigene Optik.
"""

import io
import re

import qrcode
import streamlit as st

import benutzer
import datenexport
import email_versand
import komponenten
import ratenbegrenzung
import sicherheitslog
import speicher
import zwei_faktor_krypto


_BESTAETIGUNGSTEXT = "KONTO LÖSCHEN"

# Session-State-Schlüssel für den mehrstufigen 2FA-Einrichtungs-/
# Verwaltungs-Ablauf (siehe `_zwei_faktor_abschnitt`) - alle mit
# "_2fa_konto_"-Präfix, um Kollisionen mit anderen Bereichen (z. B. der
# Login-Challenge in `benutzer.py`, die ihren eigenen, unabhängigen
# Session-State nutzt) sicher auszuschließen.
_2FA_SCHRITT_KEY = "_2fa_konto_schritt"
_2FA_SECRET_KEY = "_2fa_konto_secret"
_2FA_URI_KEY = "_2fa_konto_uri"
_2FA_ROTATION_KEY = "_2fa_konto_ist_rotation"
_2FA_BACKUP_CODES_KEY = "_2fa_konto_backup_codes"


def _2fa_code_ist_backup(eingabe):
    """Erkennt automatisch, ob ein einzelnes Eingabefeld einen TOTP- oder
    einen Backup-Code enthält (TOTP: genau 6 Ziffern nach Entfernen von
    Leerzeichen/Bindestrichen; alles andere wird als Backup-Code
    behandelt) - vermeidet einen eigenen Umschalt-Button innerhalb eines
    `st.form`-Blocks (Streamlit-Formulare unterstützen dort zuverlässig
    nur den eigentlichen Submit-Button)."""
    normalisiert = re.sub(r"[\s-]", "", eingabe or "")
    return not (normalisiert.isdigit() and len(normalisiert) == 6)


def _zweiter_faktor_bestaetigen(benutzer_id, code, aktion):
    """Prüft einen TOTP- oder Backup-Code (Format automatisch erkannt,
    siehe `_2fa_code_ist_backup`) als Re-Authentifizierung vor einer
    sicherheitskritischen Aktion (2FA deaktivieren/neu einrichten,
    Backup-Codes neu erzeugen, E-Mail ändern, Konto löschen). Bewusst EIN
    gemeinsamer Rate-Limit-Bucket je `aktion`-Kategorie statt für jede
    einzelne Aufrufstelle ein eigenes Limit - alle diese Stellen teilen
    dasselbe Bedrohungsmodell ("ein Angreifer mit gestohlenem Passwort
    versucht, den zweiten Faktor zu erraten, um eine sicherheitskritische
    Änderung zu autorisieren"). Gibt `(erfolg: bool, meldung: str)` zurück.
    """
    if not code or not code.strip():
        return False, "Bitte gib deinen Authenticator- oder Backup-Code ein."

    ist_backup = _2fa_code_ist_backup(code)
    identitaet = f"user:{benutzer_id}"

    erlaubt, wartezeit = ratenbegrenzung.pruefen(aktion, identitaet)

    if not erlaubt:
        sicherheitslog.protokollieren(
            sicherheitslog.EREIGNIS_2FA_RATE_LIMITIERT, user_id=benutzer_id, detail=aktion
        )
        return False, (
            f"Zu viele Fehlversuche. Bitte versuche es in "
            f"{ratenbegrenzung.wartezeit_text(wartezeit)} erneut."
        )

    gueltig, technische_meldung = speicher.zwei_faktor_code_pruefen(benutzer_id, code, ist_backup)
    ratenbegrenzung.versuch_aufzeichnen(aktion, identitaet, gueltig)

    if not gueltig:
        return False, technische_meldung or "Der eingegebene Code ist falsch."

    if ist_backup:
        sicherheitslog.protokollieren(sicherheitslog.EREIGNIS_BACKUP_CODE_VERWENDET, user_id=benutzer_id)

    return True, ""


def _qr_code_png(otpauth_uri):
    """Erzeugt den QR-Code AUSSCHLIESSLICH lokal (Paket `qrcode`, keine
    externe Webseite/API) und gibt ihn als PNG-Bytes zurück."""
    bild = qrcode.make(otpauth_uri)
    puffer = io.BytesIO()
    bild.save(puffer, format="PNG")
    return puffer.getvalue()


def _meldung_anzeigen(session_key):
    """Zeigt eine über einen `st.rerun()` hinweg gemerkte Erfolgs-/Fehlermeldung
    genau einmal an und entfernt sie danach wieder (`pop`) - nötig, weil
    ein Formular hier nach dem Speichern bewusst neu lädt (siehe
    Aufrufer), damit vorausgefüllte Felder die neuen Werte zeigen; eine
    `st.success`/`st.error`-Meldung VOR diesem Rerun wäre sonst beim
    nächsten Skriptlauf sofort wieder verschwunden.
    """
    eintrag = st.session_state.pop(session_key, None)

    if not eintrag:
        return

    erfolg, text = eintrag
    (st.success if erfolg else st.error)(text)


def _profil_abschnitt(benutzer_id, konto, zwei_faktor_status):
    st.markdown("### 👤 Profil")

    verifiziert_text = "✅ bestätigt" if konto["email_verified"] else "⏳ nicht bestätigt"
    mitglied_seit = (konto.get("erstellt_am") or "")[:10]
    st.caption(f"Mitglied seit {mitglied_seit} · E-Mail-Status: {verifiziert_text}")

    _meldung_anzeigen("_konto_profil_meldung")

    with st.form("konto_profil_formular"):
        neuer_benutzername = st.text_input("Benutzername", value=konto["benutzername"])
        neue_email = st.text_input("E-Mail-Adresse", value=konto["email"])
        aktuelles_passwort = st.text_input(
            "Aktuelles Passwort (zur Bestätigung erforderlich)",
            type="password",
            key="konto_profil_aktuelles_passwort",
        )

        zwei_faktor_code = ""
        if zwei_faktor_status["aktiv"]:
            zwei_faktor_code = st.text_input(
                "Zwei-Faktor-Code (TOTP oder Backup-Code)",
                key="konto_profil_2fa_code",
                help=(
                    "Da Zwei-Faktor-Authentifizierung aktiv ist, wird für "
                    "Kontodaten-Änderungen zusätzlich ein aktueller Code verlangt."
                ),
            )

        gespeichert = st.form_submit_button("Kontodaten speichern", type="primary")

    if not gespeichert:
        return

    if not aktuelles_passwort:
        st.session_state["_konto_profil_meldung"] = (
            False, "Bitte gib dein aktuelles Passwort ein."
        )
        st.rerun()

    if zwei_faktor_status["aktiv"]:
        zwei_faktor_ok, zwei_faktor_meldung = _zweiter_faktor_bestaetigen(
            benutzer_id, zwei_faktor_code, "2fa_disable"
        )

        if not zwei_faktor_ok:
            st.session_state["_konto_profil_meldung"] = (False, zwei_faktor_meldung)
            st.rerun()

    erfolg, meldung, email_geaendert = speicher.konto_aktualisieren(
        benutzer_id, aktuelles_passwort, neuer_benutzername, neue_email
    )

    if erfolg:
        neue_email_normalisiert = neue_email.strip().lower()
        benutzer.sitzung_felder_aktualisieren(
            benutzername=neuer_benutzername.strip(),
            email=neue_email_normalisiert,
            email_verified=not email_geaendert and konto["email_verified"],
        )

        if email_geaendert:
            sicherheitslog.protokollieren(
                sicherheitslog.EREIGNIS_EMAIL_GEAENDERT,
                user_id=benutzer_id,
                identitaet=neue_email_normalisiert,
            )
            roher_token = speicher.email_verifizierung_erstellen(benutzer_id, neue_email_normalisiert)
            email_versand.sende_email_geaendert(
                neue_email_normalisiert, email_versand.verifizierungs_link(roher_token)
            )
            meldung += " Deine neue E-Mail-Adresse muss noch bestätigt werden."

    st.session_state["_konto_profil_meldung"] = (erfolg, meldung)
    st.rerun()


def _verifizierung_abschnitt(benutzer_id, konto):
    if konto["email_verified"]:
        return

    st.markdown("#### ✉️ E-Mail-Bestätigung ausstehend")
    komponenten.hinweis_dezent(
        f"Wir haben eine Bestätigungs-E-Mail an **{konto['email']}** gesendet. "
        "Klicke auf den Link darin, um deine Adresse zu bestätigen. Bis "
        "dahin sind Dokument-Upload und die KI-Funktionen gesperrt (siehe "
        "„✉️ E-Mail bestätigen“ in der Navigation)."
    )

    _meldung_anzeigen("_konto_verifizierung_meldung")

    aktiv, wartezeit = ratenbegrenzung.resend_cooldown_aktiv(konto["email"])

    if st.button(
        "Bestätigungs-E-Mail erneut senden",
        key="konto_verifizierung_neu_button",
        disabled=aktiv,
        use_container_width=True,
    ):
        erfolg, text = benutzer.bestaetigungsmail_anfordern(benutzer_id, konto["email"])
        st.session_state["_konto_verifizierung_meldung"] = (erfolg, text)
        st.rerun()

    if aktiv:
        st.caption(f"Erneutes Senden möglich in {ratenbegrenzung.wartezeit_text(wartezeit)}.")


def _2fa_reset_zustand(benutzer_id=None):
    """Setzt den lokalen 2FA-Verwaltungs-Ablauf zurück (Abbrechen-Aktionen).
    Mit `benutzer_id` wird zusätzlich ein evtl. gerade erst erzeugtes,
    noch unbestätigtes Pending-Secret serverseitig sofort verworfen
    (statt auf dessen natürlichen Ablauf nach `PENDING_2FA_GUELTIGKEIT_MINUTEN`
    zu warten) - "kein halb aktiviertes 2FA" bei einem abgebrochenen Setup.
    """
    for key in (_2FA_SCHRITT_KEY, _2FA_SECRET_KEY, _2FA_URI_KEY, _2FA_ROTATION_KEY):
        st.session_state.pop(key, None)

    if benutzer_id is not None:
        speicher.zwei_faktor_setup_abbrechen(benutzer_id)


def _zwei_faktor_abschnitt(benutzer_id, status):
    st.markdown("### 🔐 Zwei-Faktor-Authentifizierung")

    if not zwei_faktor_krypto.schluessel_konfiguriert():
        komponenten.hinweis_dezent(
            "Zwei-Faktor-Authentifizierung ist auf diesem Server aktuell "
            "nicht verfügbar (fehlender oder ungültiger Verschlüsselungsschlüssel). "
            "Wende dich an den Betreiber."
        )
        return

    _meldung_anzeigen("_konto_2fa_meldung")

    # Backup-Codes-Anzeige hat IMMER Vorrang vor jedem anderen Schritt -
    # sie ist die einzige Gelegenheit, sie im Klartext zu sehen, und darf
    # nicht durch einen zwischenzeitlichen Schritt-Wechsel verloren gehen.
    if st.session_state.get(_2FA_BACKUP_CODES_KEY):
        _2fa_backup_codes_anzeigen()
        return

    schritt = st.session_state.get(_2FA_SCHRITT_KEY)

    if schritt in ("passwort", "rotation_auth"):
        _2fa_vorpruefung_ansicht(benutzer_id, ist_rotation=(schritt == "rotation_auth"))
    elif schritt == "confirm":
        _2fa_bestaetigung_ansicht(benutzer_id)
    elif schritt == "regenerieren":
        _2fa_backup_regenerieren_ansicht(benutzer_id)
    elif schritt == "deaktivieren":
        _2fa_deaktivieren_ansicht(benutzer_id)
    elif status["aktiv"]:
        _2fa_aktiv_uebersicht(benutzer_id, status)
    else:
        _2fa_inaktiv_uebersicht(status)


def _2fa_inaktiv_uebersicht(status):
    st.caption(
        "Schütze dein Konto zusätzlich mit einer Authenticator-App (z. B. "
        "Google Authenticator, Microsoft Authenticator, Authy, 1Password "
        "oder Bitwarden - jede RFC-6238-kompatible App funktioniert)."
    )

    if status["pending"]:
        st.caption("Es läuft bereits eine unbestätigte Einrichtung - starte sie unten erneut.")

    if st.button(
        "2FA einrichten", key="2fa_einrichten_start", type="primary", use_container_width=True
    ):
        st.session_state[_2FA_SCHRITT_KEY] = "passwort"
        st.session_state[_2FA_ROTATION_KEY] = False
        st.rerun()


def _2fa_aktiv_uebersicht(benutzer_id, status):
    uebrig = speicher.zwei_faktor_backup_codes_anzahl_uebrig(benutzer_id)
    st.success(
        f"Zwei-Faktor-Authentifizierung ist aktiv. Noch {uebrig} von "
        f"{speicher.BACKUP_CODES_ANZAHL} Backup-Codes verfügbar."
    )

    spalte_rotieren, spalte_regenerieren, spalte_deaktivieren = st.columns(3)

    if spalte_rotieren.button(
        "2FA neu einrichten", key="2fa_rotation_start", use_container_width=True
    ):
        st.session_state[_2FA_SCHRITT_KEY] = "rotation_auth"
        st.session_state[_2FA_ROTATION_KEY] = True
        st.rerun()

    if spalte_regenerieren.button(
        "Neue Backup-Codes erstellen", key="2fa_regen_start", use_container_width=True
    ):
        st.session_state[_2FA_SCHRITT_KEY] = "regenerieren"
        st.rerun()

    if spalte_deaktivieren.button(
        "2FA deaktivieren", key="2fa_deaktivieren_start", use_container_width=True
    ):
        st.session_state[_2FA_SCHRITT_KEY] = "deaktivieren"
        st.rerun()


def _2fa_vorpruefung_ansicht(benutzer_id, ist_rotation):
    """Passwort- (und bei einer Neu-Einrichtung/Rotation zusätzlich
    2FA-)Bestätigung, BEVOR ein neues Pending-Secret erzeugt wird - siehe
    Aufgabenstellung Abschnitt 12: "aktuelles Passwort, bestehender
    TOTP-Code oder Backup-Code" ist Voraussetzung für eine Neu-Einrichtung,
    damit nicht allein eine gekaperte, aber noch angemeldete Sitzung
    genügt, um den zweiten Faktor eines fremden Geräts unterzuschieben."""
    titel = "2FA neu einrichten" if ist_rotation else "2FA einrichten"
    st.markdown(f"#### {titel}")
    zusatz = " und einen aktuellen Authenticator- oder Backup-Code" if ist_rotation else ""
    st.caption(f"Bitte bestätige zunächst dein aktuelles Passwort{zusatz}.")

    with st.form("2fa_vorpruefung_formular"):
        passwort = st.text_input(
            "Aktuelles Passwort", type="password", key="2fa_vorpruefung_passwort"
        )
        code = (
            st.text_input("Aktueller Authenticator- oder Backup-Code", key="2fa_vorpruefung_code")
            if ist_rotation
            else ""
        )
        weiter = st.form_submit_button("Weiter", type="primary")

    if st.button("Abbrechen", key="2fa_vorpruefung_abbrechen"):
        _2fa_reset_zustand()
        st.rerun()

    if not weiter:
        return

    if not speicher.konto_passwort_gueltig(benutzer_id, passwort):
        st.error("Das aktuelle Passwort ist falsch.")
        return

    if ist_rotation:
        zwei_faktor_ok, zwei_faktor_meldung = _zweiter_faktor_bestaetigen(
            benutzer_id, code, "2fa_disable"
        )

        if not zwei_faktor_ok:
            st.error(zwei_faktor_meldung)
            return

    klartext_secret, uri = speicher.zwei_faktor_setup_starten(benutzer_id)
    st.session_state[_2FA_SECRET_KEY] = klartext_secret
    st.session_state[_2FA_URI_KEY] = uri
    st.session_state[_2FA_SCHRITT_KEY] = "confirm"
    sicherheitslog.protokollieren(sicherheitslog.EREIGNIS_2FA_SETUP_GESTARTET, user_id=benutzer_id)
    st.rerun()


def _2fa_bestaetigung_ansicht(benutzer_id):
    st.markdown("#### Authenticator-App verbinden")

    secret = st.session_state.get(_2FA_SECRET_KEY)
    uri = st.session_state.get(_2FA_URI_KEY)

    if not secret or not uri:
        st.error("Die Einrichtung ist nicht mehr gültig oder abgelaufen. Bitte starte erneut.")
        _2fa_reset_zustand(benutzer_id)
        return

    st.caption("Scanne den QR-Code mit deiner Authenticator-App:")
    st.image(_qr_code_png(uri), width=220)

    manueller_schluessel = " ".join(secret[i : i + 4] for i in range(0, len(secret), 4))
    st.caption("Oder gib diesen Schlüssel manuell in deiner App ein:")
    st.code(manueller_schluessel)

    with st.form("2fa_bestaetigung_formular"):
        code = st.text_input("6-stelliger Code aus der App", key="2fa_bestaetigung_code")
        bestaetigen = st.form_submit_button("Aktivieren", type="primary")

    if st.button("Abbrechen", key="2fa_bestaetigung_abbrechen"):
        _2fa_reset_zustand(benutzer_id)
        st.rerun()

    if not bestaetigen:
        return

    identitaet = f"user:{benutzer_id}"
    erlaubt, wartezeit = ratenbegrenzung.pruefen("2fa_setup_verify", identitaet)

    if not erlaubt:
        sicherheitslog.protokollieren(
            sicherheitslog.EREIGNIS_2FA_RATE_LIMITIERT, user_id=benutzer_id, detail="2fa_setup_verify"
        )
        st.error(
            f"Zu viele Fehlversuche. Bitte versuche es in "
            f"{ratenbegrenzung.wartezeit_text(wartezeit)} erneut."
        )
        return

    erfolg, meldung, backup_codes = speicher.zwei_faktor_setup_bestaetigen(benutzer_id, code)
    ratenbegrenzung.versuch_aufzeichnen("2fa_setup_verify", identitaet, erfolg)

    if not erfolg:
        sicherheitslog.protokollieren(
            sicherheitslog.EREIGNIS_2FA_SETUP_FEHLGESCHLAGEN, user_id=benutzer_id, erfolgreich=False
        )
        st.error(meldung)
        return

    ist_rotation = st.session_state.get(_2FA_ROTATION_KEY, False)

    if ist_rotation:
        # Andere Sitzungen (z. B. das alte, jetzt ersetzte Gerät) müssen
        # sich neu anmelden - die eigene, gerade aktive Sitzung bleibt
        # bewusst ausgenommen (Aufgabenstellung Abschnitt 12).
        speicher.sitzungen_widerrufen_fuer_benutzer(
            benutzer_id, ausser_roher_token=benutzer.aktuelle_sitzung_token()
        )
        sicherheitslog.protokollieren(sicherheitslog.EREIGNIS_2FA_SECRET_ROTIERT, user_id=benutzer_id)
    else:
        sicherheitslog.protokollieren(sicherheitslog.EREIGNIS_2FA_AKTIVIERT, user_id=benutzer_id)

    for key in (_2FA_SCHRITT_KEY, _2FA_SECRET_KEY, _2FA_URI_KEY, _2FA_ROTATION_KEY):
        st.session_state.pop(key, None)

    st.session_state[_2FA_BACKUP_CODES_KEY] = backup_codes
    st.rerun()


def _2fa_backup_codes_anzeigen():
    codes = st.session_state.get(_2FA_BACKUP_CODES_KEY) or []

    st.markdown("#### Deine Backup-Codes")
    st.warning(
        "Speichere diese Codes JETZT sicher (z. B. Passwort-Manager oder "
        "Ausdruck) - sie werden aus Sicherheitsgründen nie wieder im "
        "Klartext angezeigt. Jeder Code funktioniert nur EINMAL."
    )

    st.code("\n".join(codes))

    st.download_button(
        "Backup-Codes herunterladen (.txt)",
        data="\n".join(codes).encode("utf-8"),
        file_name="clevoriq-2fa-backup-codes.txt",
        mime="text/plain",
        key="2fa_backup_codes_download",
    )

    if st.button(
        "Ich habe die Codes sicher gespeichert",
        key="2fa_backup_codes_bestaetigt",
        type="primary",
        use_container_width=True,
    ):
        st.session_state.pop(_2FA_BACKUP_CODES_KEY, None)
        st.session_state["_konto_2fa_meldung"] = (
            True, "Zwei-Faktor-Authentifizierung ist eingerichtet."
        )
        st.rerun()


def _2fa_backup_regenerieren_ansicht(benutzer_id):
    st.markdown("#### Neue Backup-Codes erstellen")
    st.caption(
        "Erzeugt neue Backup-Codes - alle bisherigen werden dabei sofort "
        "ungültig. Bestätige mit Passwort und einem aktuellen "
        "Authenticator-Code (TOTP)."
    )

    with st.form("2fa_regen_formular"):
        passwort = st.text_input("Aktuelles Passwort", type="password", key="2fa_regen_passwort")
        code = st.text_input("Aktueller Authenticator-Code (TOTP)", key="2fa_regen_code")
        abschicken = st.form_submit_button("Neue Backup-Codes erstellen", type="primary")

    if st.button("Abbrechen", key="2fa_regen_abbrechen"):
        _2fa_reset_zustand()
        st.rerun()

    if not abschicken:
        return

    if not speicher.konto_passwort_gueltig(benutzer_id, passwort):
        st.error("Das aktuelle Passwort ist falsch.")
        return

    # Bewusst NUR TOTP zugelassen (kein Backup-Code, siehe Aufgabenstellung
    # Abschnitt 11) - verhindert, dass jemand mit nur noch übrigen
    # Backup-Codes (Authenticator verloren) sich unbegrenzt neue
    # Backup-Codes erzeugen kann, ohne je den Besitz des Authenticators
    # nachzuweisen. Wer den Authenticator verloren hat, nutzt stattdessen
    # "2FA neu einrichten" (akzeptiert dort bewusst auch Backup-Codes).
    if _2fa_code_ist_backup(code):
        st.error(
            "Für neue Backup-Codes wird ein Authenticator-Code (TOTP) "
            "benötigt, kein Backup-Code."
        )
        return

    zwei_faktor_ok, zwei_faktor_meldung = _zweiter_faktor_bestaetigen(
        benutzer_id, code, "backup_codes_regenerate"
    )

    if not zwei_faktor_ok:
        st.error(zwei_faktor_meldung)
        return

    neue_codes = speicher.zwei_faktor_backup_codes_neu_erzeugen(benutzer_id)
    sicherheitslog.protokollieren(sicherheitslog.EREIGNIS_BACKUP_CODES_NEU_ERZEUGT, user_id=benutzer_id)

    st.session_state.pop(_2FA_SCHRITT_KEY, None)
    st.session_state[_2FA_BACKUP_CODES_KEY] = neue_codes
    st.rerun()


def _2fa_deaktivieren_ansicht(benutzer_id):
    st.markdown("#### 2FA deaktivieren")
    st.caption(
        "Bestätige mit Passwort und einem aktuellen Authenticator- oder "
        "Backup-Code. Danach werden alle anderen Sitzungen dieses Kontos "
        "abgemeldet."
    )

    with st.form("2fa_deaktivieren_formular"):
        passwort = st.text_input(
            "Aktuelles Passwort", type="password", key="2fa_deaktivieren_passwort"
        )
        code = st.text_input("Aktueller Authenticator- oder Backup-Code", key="2fa_deaktivieren_code")
        abschicken = st.form_submit_button("2FA endgültig deaktivieren", type="primary")

    if st.button("Abbrechen", key="2fa_deaktivieren_abbrechen"):
        _2fa_reset_zustand()
        st.rerun()

    if not abschicken:
        return

    if not speicher.konto_passwort_gueltig(benutzer_id, passwort):
        st.error("Das aktuelle Passwort ist falsch.")
        return

    zwei_faktor_ok, zwei_faktor_meldung = _zweiter_faktor_bestaetigen(benutzer_id, code, "2fa_disable")

    if not zwei_faktor_ok:
        st.error(zwei_faktor_meldung)
        return

    speicher.zwei_faktor_deaktivieren(benutzer_id)
    # Alle Sitzungen widerrufen und die EIGENE, gerade genutzte sofort
    # neu ausstellen (Session-Fixation-Vorsorge wie bei jedem Login) -
    # "aktuelle Session sicher behandeln / gegebenenfalls neu ausstellen"
    # (Aufgabenstellung Abschnitt 10).
    benutzer.sitzung_neu_ausstellen(benutzer_id)
    sicherheitslog.protokollieren(sicherheitslog.EREIGNIS_2FA_DEAKTIVIERT, user_id=benutzer_id)

    st.session_state.pop(_2FA_SCHRITT_KEY, None)
    st.session_state["_konto_2fa_meldung"] = (
        True, "Zwei-Faktor-Authentifizierung wurde deaktiviert."
    )
    st.rerun()


def _passwort_abschnitt(benutzer_id):
    st.markdown("### 🔒 Passwort ändern")

    _meldung_anzeigen("_konto_passwort_meldung")

    with st.form("konto_passwort_formular"):
        aktuelles = st.text_input("Aktuelles Passwort", type="password", key="konto_pw_aktuell")
        neues = st.text_input("Neues Passwort", type="password", key="konto_pw_neu")
        wiederholt = st.text_input(
            "Neues Passwort wiederholen", type="password", key="konto_pw_wiederholt"
        )
        geaendert = st.form_submit_button("Passwort ändern", type="primary")

    if not geaendert:
        return

    if neues != wiederholt:
        st.session_state["_konto_passwort_meldung"] = (
            False, "Die neuen Passwörter stimmen nicht überein."
        )
        st.rerun()

    erfolg, meldung = speicher.passwort_aendern(benutzer_id, aktuelles, neues)

    if erfolg:
        # Alle ANDEREN Sitzungen dieses Benutzers ungültig machen (z. B.
        # ein weiteres offenes Gerät/Tab) - die eigene, gerade aktive
        # Sitzung bleibt bewusst ausgenommen, damit die Person, die die
        # Änderung selbst vorgenommen hat, nicht sofort ausgeloggt wird.
        speicher.sitzungen_widerrufen_fuer_benutzer(
            benutzer_id, ausser_roher_token=benutzer.aktuelle_sitzung_token()
        )
        sicherheitslog.protokollieren(sicherheitslog.EREIGNIS_PASSWORT_GEAENDERT, user_id=benutzer_id)

        konto = speicher.benutzer_konto_daten(benutzer_id)

        if konto:
            email_versand.sende_passwort_geaendert(konto["email"])

    st.session_state["_konto_passwort_meldung"] = (erfolg, meldung)
    st.rerun()


def _export_abschnitt(benutzer_id):
    st.markdown("### 📦 Daten exportieren")
    st.caption(
        "Lade eine strukturierte ZIP-Datei mit deinen Kontodaten, "
        "Dokumenten (inkl. Originaldateien), Chats und Nachrichten herunter."
    )

    zip_bytes = datenexport.zip_erstellen(benutzer_id)

    st.download_button(
        "Meine Daten exportieren",
        data=zip_bytes,
        file_name="Clevoriq-Datenexport.zip",
        mime="application/zip",
        key="konto_export_button",
    )


def _loeschen_abschnitt(benutzer_id, konto, zwei_faktor_status):
    st.markdown("### 🗑️ Konto löschen")

    with st.container(border=True):
        st.markdown("**Konto endgültig löschen**")
        st.caption(
            "Löscht dein Konto sowie ALLE zugehörigen Daten unwiderruflich: "
            "alle Dokumente und deren Originaldateien, alle gespeicherten "
            "Textausschnitte und Embeddings, alle Chats und Nachrichten. "
            "Diese Aktion kann nicht rückgängig gemacht werden."
        )

        aktuelles_passwort = st.text_input(
            "Aktuelles Passwort", type="password", key="konto_loeschen_passwort"
        )

        zwei_faktor_code = ""
        if zwei_faktor_status["aktiv"]:
            zwei_faktor_code = st.text_input(
                "Zwei-Faktor-Code (TOTP oder Backup-Code)",
                key="konto_loeschen_2fa_code",
            )

        bestaetigungstext = st.text_input(
            f"Gib zur Bestätigung „{_BESTAETIGUNGSTEXT}“ ein",
            key="konto_loeschen_bestaetigung",
        )

        bereit = (
            bool(aktuelles_passwort)
            and (not zwei_faktor_status["aktiv"] or bool(zwei_faktor_code))
            and bestaetigungstext.strip() == _BESTAETIGUNGSTEXT
        )

        if not bereit:
            zusatz = " und ein aktueller 2FA-Code" if zwei_faktor_status["aktiv"] else ""
            st.caption(
                f"Passwort{zusatz} sowie der exakte Text „{_BESTAETIGUNGSTEXT}“ sind "
                "erforderlich, um fortzufahren."
            )

        if st.button(
            "Konto endgültig löschen",
            key="konto_confirm_del_endgueltig",
            disabled=not bereit,
            use_container_width=True,
        ):
            if not speicher.konto_passwort_gueltig(benutzer_id, aktuelles_passwort):
                st.error("Das aktuelle Passwort ist falsch.")
                return

            if zwei_faktor_status["aktiv"]:
                zwei_faktor_ok, zwei_faktor_meldung = _zweiter_faktor_bestaetigen(
                    benutzer_id, zwei_faktor_code, "2fa_disable"
                )

                if not zwei_faktor_ok:
                    st.error(zwei_faktor_meldung)
                    return

            empfaenger_email = konto["email"]
            sicherheitslog.protokollieren(
                sicherheitslog.EREIGNIS_KONTO_GELOESCHT, user_id=benutzer_id
            )
            speicher.konto_endgueltig_loeschen(benutzer_id)
            email_versand.sende_konto_geloescht(empfaenger_email)
            benutzer.abmelden_nach_kontoloeschung()


def seite(benutzer_id):
    """Rendert den kompletten Bereich "⚙️ Konto & Sicherheit"."""
    komponenten.seiten_hero(
        "⚙",
        "Konto & Sicherheit",
        "Verwalte dein Clevoriq-Konto und deine Sicherheitseinstellungen.",
    )

    konto = speicher.benutzer_konto_daten(benutzer_id)

    if not konto:
        st.error("Konto nicht gefunden.")
        return

    zwei_faktor_status = speicher.zwei_faktor_status(benutzer_id)

    _profil_abschnitt(benutzer_id, konto, zwei_faktor_status)
    _verifizierung_abschnitt(benutzer_id, konto)

    st.divider()
    _zwei_faktor_abschnitt(benutzer_id, zwei_faktor_status)

    st.divider()
    _passwort_abschnitt(benutzer_id)

    st.divider()
    _export_abschnitt(benutzer_id)

    st.divider()
    _loeschen_abschnitt(benutzer_id, konto, zwei_faktor_status)
