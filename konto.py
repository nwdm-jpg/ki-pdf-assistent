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

import streamlit as st

import benutzer
import datenexport
import email_versand
import komponenten
import ratenbegrenzung
import sicherheitslog
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

    _profil_abschnitt(benutzer_id, konto)
    _verifizierung_abschnitt(benutzer_id, konto)

    st.divider()
    _passwort_abschnitt(benutzer_id)

    st.divider()
    _export_abschnitt(benutzer_id)

    st.divider()
    _loeschen_abschnitt(benutzer_id, konto)
