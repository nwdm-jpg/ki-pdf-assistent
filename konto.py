"""Streamlit-Oberfläche für den Bereich "⚙️ Konto & Sicherheit".

Bündelt Profilbearbeitung, E-Mail-Bestätigung, Passwortänderung,
Datenexport und Kontolöschung an einem Ort. Jede Funktion hier erhält
ausschließlich den `benutzer_id` der angemeldeten Sitzung (von
`web_app.py` durchgereicht, nie ein aus einem Formularfeld gelesener
Wert) und delegiert jede eigentliche Prüfung/Änderung an `speicher.py` -
dieselbe Schichtentrennung wie im Rest der App (siehe `speicher.py`s
Prinzip der strikten Datentrennung). Wiederverwendet bewusst dieselben
AVENLOQ-Bausteine wie die übrigen Bereiche (`komponenten.seiten_kopf`,
`st.form_submit_button(type="primary")`, `komponenten.hinweis_dezent`,
`st.container(border=True)`) - keine eigene Optik.
"""

import streamlit as st

import benutzer
import datenexport
import email_versand
import komponenten
import speicher


_BESTAETIGUNGSTEXT = "KONTO LÖSCHEN"


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


def _profil_abschnitt(benutzer_id, konto):
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
        gespeichert = st.form_submit_button("Kontodaten speichern", type="primary")

    if not gespeichert:
        return

    if not aktuelles_passwort:
        st.session_state["_konto_profil_meldung"] = (
            False, "Bitte gib dein aktuelles Passwort ein."
        )
        st.rerun()

    erfolg, meldung, email_geaendert = speicher.konto_aktualisieren(
        benutzer_id, aktuelles_passwort, neuer_benutzername, neue_email
    )

    if erfolg:
        benutzer.sitzung_felder_aktualisieren(
            benutzername=neuer_benutzername.strip(),
            email=neue_email.strip().lower(),
        )

        if email_geaendert:
            roher_token = speicher.email_verifizierung_erstellen(
                benutzer_id, neue_email.strip().lower()
            )
            st.session_state["konto_verifizierung_token"] = roher_token
            # Entwicklungsmodus: kein Anbieter angebunden, `email_versand`
            # loggt die Nachricht nur (siehe dortige Doku) - der Nutzer
            # bestätigt stattdessen unten direkt im Entwicklungsmodus.
            email_versand.sende_email_geaendert(
                neue_email.strip().lower(),
                "Entwicklungsmodus: Bestätige die neue Adresse im Bereich "
                "„Konto & Sicherheit“ unter „E-Mail-Bestätigung“.",
            )
            meldung += " Deine neue E-Mail-Adresse ist noch nicht bestätigt."

    st.session_state["_konto_profil_meldung"] = (erfolg, meldung)
    st.rerun()


def _verifizierung_abschnitt(benutzer_id, konto):
    if konto["email_verified"]:
        return

    st.markdown("#### ✉️ E-Mail-Bestätigung ausstehend")
    komponenten.hinweis_dezent(
        "Es ist noch kein E-Mail-Versand angebunden (siehe „email_versand.py“) "
        "- die Bestätigung wird deshalb aktuell nicht erzwungen und "
        "bestehende Entwicklungs-Konten werden dadurch nicht gesperrt. Im "
        "Entwicklungsmodus kannst du die Bestätigung hier direkt auslösen, "
        "so wie es später ein Klick auf den zugesendeten E-Mail-Link tun würde."
    )

    _meldung_anzeigen("_konto_verifizierung_meldung")

    token = st.session_state.get("konto_verifizierung_token")
    spalte_bestaetigen, spalte_neu = st.columns(2)

    if spalte_bestaetigen.button(
        "E-Mail jetzt bestätigen (Entwicklungsmodus)",
        key="konto_verifizieren_button",
        disabled=not token,
        use_container_width=True,
    ):
        erfolg, meldung = speicher.email_verifizierung_bestaetigen(token)
        st.session_state.pop("konto_verifizierung_token", None)
        st.session_state["_konto_verifizierung_meldung"] = (erfolg, meldung)
        st.rerun()

    if spalte_neu.button(
        "Neuen Bestätigungslink anfordern",
        key="konto_verifizierung_neu_button",
        use_container_width=True,
    ):
        neuer_token = speicher.email_verifizierung_erstellen(benutzer_id, konto["email"])
        st.session_state["konto_verifizierung_token"] = neuer_token
        email_versand.sende_registrierung_verifizierung(
            konto["email"],
            "Entwicklungsmodus: Bestätige die Adresse im Bereich "
            "„Konto & Sicherheit“ unter „E-Mail-Bestätigung“.",
        )
        st.session_state["_konto_verifizierung_meldung"] = (
            True, "Ein neuer Bestätigungslink wurde erzeugt (Entwicklungsmodus)."
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
        file_name="AVENLOQ-Datenexport.zip",
        mime="application/zip",
        key="konto_export_button",
    )


def _loeschen_abschnitt(benutzer_id, konto):
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
        bestaetigungstext = st.text_input(
            f"Gib zur Bestätigung „{_BESTAETIGUNGSTEXT}“ ein",
            key="konto_loeschen_bestaetigung",
        )

        bereit = bool(aktuelles_passwort) and bestaetigungstext.strip() == _BESTAETIGUNGSTEXT

        if not bereit:
            st.caption(
                f"Passwort und der exakte Text „{_BESTAETIGUNGSTEXT}“ sind "
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

            empfaenger_email = konto["email"]
            speicher.konto_endgueltig_loeschen(benutzer_id)
            email_versand.sende_konto_geloescht(empfaenger_email)
            benutzer.abmelden_nach_kontoloeschung()


def seite(benutzer_id):
    """Rendert den kompletten Bereich "⚙️ Konto & Sicherheit"."""
    komponenten.seiten_kopf(
        benutzer.BEREICH_KONTO,
        "Verwalte dein AVENLOQ-Konto und deine Sicherheitseinstellungen.",
    )

    konto = speicher.benutzer_konto_daten(benutzer_id)

    if not konto:
        st.error("Konto nicht gefunden.")
        return

    _profil_abschnitt(benutzer_id, konto)
    _verifizierung_abschnitt(benutzer_id, konto)

    st.divider()
    _passwort_abschnitt(benutzer_id)

    st.divider()
    _export_abschnitt(benutzer_id)

    st.divider()
    _loeschen_abschnitt(benutzer_id, konto)
