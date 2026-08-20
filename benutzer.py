"""Streamlit-Anmelde-/Registrierungs-Oberfläche und Sitzungsverwaltung.

Trennt die Authentifizierungs-UI und den Sitzungszustand ("wer ist
aktuell angemeldet") von der reinen Krypto-/Validierungslogik
(`auth.py`) und der Datenbank-Persistenz (`speicher.py`) - `web_app.py`
ruft nur die Funktionen dieses Moduls auf (`ist_angemeldet`,
`authentifizierung_anzeigen`, `aktueller_benutzer_id`, `konto_bereich`,
`abmelden`, `sitzung_gueltig_pruefen`, `verifizierung_seite`) und enthält
selbst keine Login-/Registrierungs-/Sitzungsdetails.

Sitzung: `st.session_state` bleibt der EINZIGE Ort, an dem der rohe
Sitzungs-Token dieses Browser-Tabs liegt (kein Cookie, nicht in der
URL) - ein neuer Tab/eine neue Streamlit-Sitzung verlangt weiterhin
immer erneut eine Anmeldung. NEU: zusätzlich existiert je Login ein
serverseitiger Datensatz (`speicher.sitzung_erstellen`/
`sitzung_pruefen_und_aktualisieren`), gegen den `sitzung_gueltig_pruefen()`
bei JEDEM Lauf validiert - das erlaubt es, eine Sitzung aktiv von
"außen" zu beenden (anderer Tab/Logout, Passwort-Änderung/-Reset,
Kontolöschung), was mit reinem `st.session_state` nicht möglich wäre.
Bekannte, im Streamlit-Modell verbleibende Grenze: kein HttpOnly/Secure-
Cookie, keine serverseitige Bindung an den Browser selbst - siehe
Docstring von `speicher.sitzung_erstellen` für Details.

Wiederverwendet für die Login-/Registrierungsseite bewusst dieselben
Bausteine wie der Rest von Clevoriq (`komponenten.marke_kopf`,
`komponenten.marke_tagline`, `st.form_submit_button(type="primary")` für
den Farbverlauf-Button, `st.container(border=True)` für die Karte) -
keine eigene Farb-/Typografie-Definition, damit die Optik automatisch
konsistent bleibt und nicht zweimal gepflegt werden muss.
"""

import streamlit as st

import auth
import email_versand
import komponenten
import ratenbegrenzung
import sicherheitslog
import speicher


_SESSION_KEY = "benutzer"
_MODUS_KEY = "auth_modus"
_SITZUNG_TOKEN_KEY = "_sitzung_token"

# Eigener, kleiner Bereich für unbestätigte Konten (siehe
# `verifizierung_seite`/`web_app.py`s Zugriffsbeschränkung) - kein
# Eintrag in der Haupt-Sidebar-Navigation für bereits bestätigte Konten.
BEREICH_VERIFIZIERUNG = "✉️ E-Mail bestätigen"
BEREICH_KONTO = "⚙ Konto & Sicherheit"

# Bewusst EIN fester Text, unabhängig davon, ob zur eingegebenen E-Mail
# tatsächlich ein Konto existiert (siehe `_passwort_reset_anfordern` und
# CLAUDE.md "keine Account Enumeration") - als Modul-Konstante, damit
# beide Aufrufer (Formular-Handler, Tests) exakt denselben Text verwenden.
PASSWORT_RESET_MELDUNG = (
    "Falls ein Konto mit dieser E-Mail-Adresse existiert, wurde eine "
    "Nachricht zum Zurücksetzen des Passworts versendet."
)


def ist_angemeldet():
    return st.session_state.get(_SESSION_KEY) is not None


def aktueller_benutzer():
    """Gibt {"id", "benutzername", "email", "email_verified"} des
    angemeldeten Benutzers zurück (oder None)."""
    return st.session_state.get(_SESSION_KEY)


def aktueller_benutzer_id():
    benutzer = aktueller_benutzer()
    return benutzer["id"] if benutzer else None


def email_verifiziert():
    """True, wenn der angemeldete Benutzer seine E-Mail bestätigt hat
    (oder kein Benutzer angemeldet ist - defensiv, sollte in der Praxis
    von `web_app.py` nie in diesem Zustand abgefragt werden)."""
    benutzer = aktueller_benutzer()
    return bool(benutzer and benutzer.get("email_verified"))


def aktuelle_sitzung_token():
    """Roher Sitzungs-Token dieses Laufs (oder None) - u. a. genutzt,
    damit eine selbst durchgeführte Passwort-Änderung (`konto.py`) die
    EIGENE, gerade aktive Sitzung von der Massen-Invalidierung ausnehmen
    kann (siehe `speicher.sitzungen_widerrufen_fuer_benutzer`)."""
    return st.session_state.get(_SITZUNG_TOKEN_KEY)


def sitzung_gueltig_pruefen():
    """Validiert die serverseitige Sitzung dieses Laufs gegen die DB
    (widerrufen? abgelaufen? inaktiv?) - MUSS von `web_app.py` bei JEDEM
    Lauf aufgerufen werden, solange `ist_angemeldet()` True liefert.

    Bei Ungültigkeit wird die Sitzung vollständig beendet (wie
    `abmelden()`), aber mit einer erklärenden Meldung statt eines
    stillen Abmeldens - deckt u. a. ab: Logout in einem anderen Tab,
    eine durchgeführte Passwort-Änderung/-Reset, eine Kontolöschung,
    Ablauf der maximalen Sitzungsdauer oder Inaktivitäts-Timeout.
    Prüft außerdem, dass der gültige Token tatsächlich zur SESSION-Zeile
    des aktuell im `st.session_state` hinterlegten Benutzers gehört -
    eine Sitzung kann dadurch nie (auch nicht durch einen Programmfehler)
    für einen anderen Benutzer als den ursprünglich angemeldeten gelten.
    """
    if not ist_angemeldet():
        return

    erwartete_benutzer_id = aktueller_benutzer_id()
    gueltige_benutzer_id = speicher.sitzung_pruefen_und_aktualisieren(aktuelle_sitzung_token())

    if gueltige_benutzer_id is None or gueltige_benutzer_id != erwartete_benutzer_id:
        sicherheitslog.protokollieren(
            sicherheitslog.EREIGNIS_SITZUNG_ABGELAUFEN, user_id=erwartete_benutzer_id
        )
        st.session_state.clear()
        st.session_state["_sitzung_abgelaufen_hinweis"] = True
        st.rerun()


def _sitzung_anmelden(benutzer_row):
    """Setzt die Sitzung für ein erfolgreich authentifiziertes Konto -
    IMMER mit einem frisch erzeugten serverseitigen Sitzungs-Token
    (siehe `speicher.sitzung_erstellen`), nie einer Wiederverwendung
    eines bestehenden - das verhindert Session-Fixation strukturell."""
    st.session_state[_SESSION_KEY] = {
        "id": benutzer_row["id"],
        "benutzername": benutzer_row["benutzername"],
        "email": benutzer_row["email"],
        "email_verified": bool(benutzer_row["email_verified"]),
    }
    st.session_state[_SITZUNG_TOKEN_KEY] = speicher.sitzung_erstellen(benutzer_row["id"])


def sitzung_email_verifiziert_setzen():
    """Markiert die aktuell angemeldete Sitzung als e-mail-bestätigt -
    NUR für den bereits angemeldeten Benutzer dieses Laufs (niemals für
    eine andere `user_id`, siehe Aufrufstelle in `web_app.py`)."""
    sitzung_felder_aktualisieren(email_verified=True)


def abmelden():
    """Beendet die Sitzung vollständig (inkl. serverseitigem Widerruf)
    und kehrt zum Anmeldebildschirm zurück.

    Bewusst ein kompletter `st.session_state.clear()` statt einzelner
    Schlüssel: Die App verteilt Sitzungszustand über viele Keys
    (aktueller Chat, Dokumentauswahl je Bereich, Analyse-/
    Prüfungsergebnisse, Formular-Widget-States) - nur ein vollständiges
    Zurücksetzen garantiert, dass nach einer Abmeldung (bzw. Anmeldung
    als anderer Benutzer) nichts vom vorherigen Benutzer sichtbar
    bleibt. Der serverseitige Widerruf VOR dem Zurücksetzen sorgt
    zusätzlich dafür, dass der Sitzungs-Token dieses Laufs auch dann
    ungültig bleibt, wenn er (z. B. über die Browser-Historie) erneut
    verwendet würde.
    """
    benutzer_id = aktueller_benutzer_id()
    speicher.sitzung_widerrufen(aktuelle_sitzung_token())

    if benutzer_id:
        sicherheitslog.protokollieren(sicherheitslog.EREIGNIS_LOGOUT, user_id=benutzer_id)

    st.session_state.clear()
    st.rerun()


def abmelden_nach_kontoloeschung():
    """Wie `abmelden()`, aber nach einer endgültigen Kontolöschung
    (`konto.py`) - derselbe vollständige `st.session_state.clear()`, damit
    keinerlei Zustand des gelöschten Kontos zurückbleibt, zusätzlich mit
    einem einmaligen Hinweis-Flag, das `authentifizierung_anzeigen()`
    direkt nach dem Zurücksetzen einmalig anzeigt und danach selbst
    wieder entfernt (`st.session_state.pop`). Ein expliziter Sitzungs-
    Widerruf ist hier nicht nötig: `speicher.konto_endgueltig_loeschen`
    löscht die `benutzer`-Zeile, wodurch alle ihre `sessions`-Zeilen
    bereits per `ON DELETE CASCADE` entfernt werden.
    """
    st.session_state.clear()
    st.session_state["_konto_geloescht_hinweis"] = True
    st.rerun()


def sitzung_felder_aktualisieren(**felder):
    """Aktualisiert einzelne Felder (z. B. `benutzername`/`email`) im
    Sitzungs-Dict des angemeldeten Benutzers, NACHDEM `speicher.konto_aktualisieren`
    die Änderung bereits in der Datenbank bestätigt hat - damit die UI
    (z. B. die Sidebar-Anzeige "👤 <benutzername>") die neuen Werte sofort
    zeigt, ohne dass sich der Benutzer erneut anmelden muss. Rein additiv:
    unbekannte Felder werden ignoriert, falls `aktueller_benutzer()` None
    ist (sollte hier nie vorkommen, da nur nach erfolgreichem Update
    aufgerufen).
    """
    benutzer = aktueller_benutzer()

    if not benutzer:
        return

    benutzer.update(felder)
    st.session_state[_SESSION_KEY] = benutzer


def _login_versuchen(login_wert, passwort):
    benutzer = speicher.benutzer_nach_login(login_wert)

    if benutzer and auth.passwort_pruefen(passwort, benutzer["passwort_hash"]):
        _sitzung_anmelden(benutzer)
        # Nur bei tatsächlich erfolgreichem Login, nicht bei jedem
        # Skriptlauf - Grundlage für eine künftige Inaktivitäts-Richtlinie
        # (siehe speicher.letzten_login_aktualisieren).
        speicher.letzten_login_aktualisieren(benutzer["id"])
        return True

    return False


def _registrieren(benutzername, email, passwort, passwort_wiederholen):
    """Validiert die Eingaben und legt bei Erfolg das Konto an.

    Gibt eine Liste deutscher Fehlermeldungen zurück (leer = Erfolg).
    Prüft Eindeutigkeit vorab explizit (für konkrete Meldungen), fängt
    aber zusätzlich einen möglichen `IntegrityError` beim tatsächlichen
    Anlegen ab - das deckt die seltene Race-Condition ab, dass zwei
    Registrierungen mit demselben Namen/derselben E-Mail nahezu
    gleichzeitig eintreffen, ohne eine technische Fehlermeldung
    preiszugeben.
    """
    fehler = []
    benutzername = (benutzername or "").strip()
    email = (email or "").strip()

    if not benutzername:
        fehler.append("Bitte gib einen Benutzernamen ein.")
    elif not auth.benutzername_gueltig(benutzername):
        fehler.append(
            "Der Benutzername darf nur Buchstaben, Ziffern, „_“, „.“ oder "
            "„-“ enthalten (3–32 Zeichen)."
        )
    elif not speicher.benutzername_frei(benutzername):
        fehler.append("Dieser Benutzername ist bereits vergeben.")

    if not email:
        fehler.append("Bitte gib eine E-Mail-Adresse ein.")
    elif not auth.email_gueltig(email):
        fehler.append("Bitte gib eine gültige E-Mail-Adresse ein.")
    elif not speicher.email_frei(email):
        fehler.append("Diese E-Mail-Adresse wird bereits verwendet.")

    if not auth.passwort_stark_genug(passwort):
        fehler.append(
            f"Das Passwort muss mindestens {auth.MINDEST_PASSWORT_LAENGE} Zeichen enthalten."
        )
    elif passwort != passwort_wiederholen:
        fehler.append("Die Passwörter stimmen nicht überein.")

    if fehler:
        return fehler

    try:
        neue_id = speicher.benutzer_erstellen(benutzername, email, passwort)
    except Exception:
        return ["Benutzername oder E-Mail-Adresse ist bereits vergeben."]

    email = email.lower()
    benutzer_row = {"id": neue_id, "benutzername": benutzername, "email": email, "email_verified": 0}
    _sitzung_anmelden(benutzer_row)
    # Registrierung meldet direkt an (siehe unten) - zählt für die
    # Aktivitäts-Nachverfolgung als erster Login.
    speicher.letzten_login_aktualisieren(neue_id)

    token = speicher.email_verifizierung_erstellen(neue_id, email)
    email_versand.sende_registrierung_verifizierung(email, email_versand.verifizierungs_link(token))
    sicherheitslog.protokollieren(sicherheitslog.EREIGNIS_REGISTRIERUNG, user_id=neue_id, identitaet=email)

    return []


def _wartezeit_text(sekunden):
    return ratenbegrenzung.wartezeit_text(sekunden)


def bestaetigungsmail_anfordern(benutzer_id, email):
    """Erzeugt und versendet einen neuen E-Mail-Verifizierungslink, nach
    Cooldown-/Rate-Limit-Prüfung. Gibt `(erfolg: bool, meldung: str)`
    zurück - genutzt sowohl vom eigenen Verifizierungs-Bereich
    (`verifizierung_seite`) als auch von Konto & Sicherheit (`konto.py`),
    damit diese Logik nur an einer Stelle existiert.
    """
    aktiv, wartezeit = ratenbegrenzung.resend_cooldown_aktiv(email)

    if aktiv:
        return False, f"Bitte warte noch {_wartezeit_text(wartezeit)}, bevor du erneut eine E-Mail anforderst."

    erlaubt, wartezeit = ratenbegrenzung.pruefen("resend_verification", email)

    if not erlaubt:
        sicherheitslog.protokollieren(
            sicherheitslog.EREIGNIS_RATE_LIMIT, user_id=benutzer_id, identitaet=email, detail="resend_verification"
        )
        return False, f"Zu viele Anfragen. Bitte versuche es in {_wartezeit_text(wartezeit)} erneut."

    token = speicher.email_verifizierung_erstellen(benutzer_id, email)
    email_versand.sende_registrierung_verifizierung(email, email_versand.verifizierungs_link(token))
    ratenbegrenzung.versuch_aufzeichnen("resend_verification", email, True)
    sicherheitslog.protokollieren(
        sicherheitslog.EREIGNIS_VERIFIZIERUNG_ANGEFORDERT, user_id=benutzer_id, identitaet=email
    )

    return True, "Eine neue Bestätigungs-E-Mail wurde gesendet."


def _passwort_reset_anfordern(email):
    """Fordert (falls möglich) einen Passwort-Reset an und gibt IMMER
    dieselbe, generische Meldung zurück (`PASSWORT_RESET_MELDUNG`) -
    unabhängig davon, ob zu `email` ein Konto existiert (siehe
    CLAUDE.md "keine Account Enumeration"). Rate-Limiting läuft VOR der
    eigentlichen Anfrage und wirkt ebenfalls identisch in beiden Fällen,
    da es rein über die eingegebene Identität schlüsselt, nicht über ein
    tatsächlich existierendes Konto.
    """
    erlaubt, _wartezeit = ratenbegrenzung.pruefen("password_reset_request", email)

    if erlaubt:
        token, konto_email = speicher.passwort_reset_anfordern(email)
        ratenbegrenzung.versuch_aufzeichnen("password_reset_request", email, True)
        sicherheitslog.protokollieren(
            sicherheitslog.EREIGNIS_PASSWORT_RESET_ANGEFORDERT,
            identitaet=email,
            erfolgreich=token is not None,
        )

        if token:
            email_versand.sende_passwort_reset(konto_email, email_versand.reset_link(token))

    return PASSWORT_RESET_MELDUNG


def _login_formular():
    st.markdown("### Willkommen zurück")
    st.caption("Mit deinem Clevoriq-Konto anmelden.")

    with st.form("login_formular"):
        login_wert = st.text_input("E-Mail oder Benutzername")
        passwort = st.text_input("Passwort", type="password")
        abgeschickt = st.form_submit_button(
            "Anmelden", type="primary", use_container_width=True
        )

    if abgeschickt:
        if not login_wert.strip() or not passwort:
            st.error("Bitte E-Mail/Benutzername und Passwort eingeben.")
        else:
            erlaubt, wartezeit = ratenbegrenzung.pruefen("login", login_wert)

            if not erlaubt:
                sicherheitslog.protokollieren(
                    sicherheitslog.EREIGNIS_RATE_LIMIT, identitaet=login_wert, detail="login"
                )
                st.error(
                    f"Zu viele Anmeldeversuche. Bitte versuche es in "
                    f"{_wartezeit_text(wartezeit)} erneut."
                )
            elif _login_versuchen(login_wert, passwort):
                sicherheitslog.protokollieren(
                    sicherheitslog.EREIGNIS_LOGIN_ERFOLG,
                    user_id=aktueller_benutzer_id(),
                    identitaet=login_wert,
                )
                st.rerun()
            else:
                ratenbegrenzung.versuch_aufzeichnen("login", login_wert, False)
                sicherheitslog.protokollieren(
                    sicherheitslog.EREIGNIS_LOGIN_FEHLGESCHLAGEN, identitaet=login_wert, erfolgreich=False
                )
                # Bewusst unspezifisch - verrät nicht, ob der Benutzername/die
                # E-Mail überhaupt existiert.
                st.error("Benutzername/E-Mail oder Passwort ist falsch.")

    spalte_register, spalte_vergessen = st.columns(2)

    if spalte_register.button("Noch kein Konto? Registrieren", key="zu_registrieren", use_container_width=True):
        st.session_state[_MODUS_KEY] = "register"
        st.rerun()

    if spalte_vergessen.button("Passwort vergessen?", key="zu_passwort_vergessen", use_container_width=True):
        st.session_state[_MODUS_KEY] = "passwort_vergessen"
        st.rerun()


def _register_formular():
    st.markdown("### Konto erstellen")
    st.caption("Lege dein Clevoriq-Konto an.")

    with st.form("register_formular"):
        benutzername = st.text_input("Benutzername")
        email = st.text_input("E-Mail")
        passwort = st.text_input("Passwort", type="password")
        passwort_wiederholen = st.text_input("Passwort wiederholen", type="password")
        abgeschickt = st.form_submit_button(
            "Konto erstellen", type="primary", use_container_width=True
        )

    if abgeschickt:
        erlaubt, wartezeit = ratenbegrenzung.pruefen("register", email)

        if not erlaubt:
            sicherheitslog.protokollieren(
                sicherheitslog.EREIGNIS_RATE_LIMIT, identitaet=email, detail="register"
            )
            st.error(f"Zu viele Registrierungen. Bitte versuche es in {_wartezeit_text(wartezeit)} erneut.")
        else:
            ratenbegrenzung.versuch_aufzeichnen("register", email, True)
            fehler = _registrieren(benutzername, email, passwort, passwort_wiederholen)

            if fehler:
                for meldung in fehler:
                    st.error(meldung)
            else:
                st.rerun()

    if st.button("Bereits registriert? Anmelden", key="zu_login", use_container_width=True):
        st.session_state[_MODUS_KEY] = "login"
        st.rerun()


def _passwort_vergessen_formular():
    st.markdown("### Passwort vergessen")
    st.caption("Gib deine E-Mail-Adresse ein, um dein Passwort zurückzusetzen.")

    with st.form("passwort_vergessen_formular"):
        email = st.text_input("E-Mail-Adresse")
        abgeschickt = st.form_submit_button(
            "Link zum Zurücksetzen senden", type="primary", use_container_width=True
        )

    if abgeschickt:
        if not email.strip():
            st.error("Bitte gib eine E-Mail-Adresse ein.")
        else:
            meldung = _passwort_reset_anfordern(email)
            st.success(meldung)

    if st.button("Zurück zur Anmeldung", key="zu_login_von_vergessen", use_container_width=True):
        st.session_state[_MODUS_KEY] = "login"
        st.rerun()


def reset_passwort_seite(roher_token):
    """Rendert die "Neues Passwort festlegen"-Seite für einen per E-Mail
    zugestellten Reset-Link - AUSSERHALB jeder Anmelde-Prüfung aufrufbar
    (`web_app.py` ruft diese Funktion VOR der Login-Schranke auf, siehe
    dortige Verarbeitung von `st.query_params["reset_token"]`), da dieser
    Ablauf per Definition ohne bestehende Anmeldung funktionieren muss.

    Berührt bewusst NIE `st.session_state[_SESSION_KEY]` - eine evtl. in
    einem anderen Tab bestehende, bereits angemeldete Sitzung eines
    ANDEREN Kontos bleibt dadurch unangetastet und wird insbesondere
    nicht versehentlich dem Konto des eingelösten Tokens zugeordnet
    (siehe CLAUDE.md-Anforderung zur Sitzungs-Trennung). Nach Erfolg
    macht `speicher.passwort_reset_einloesen` bereits alle Sitzungen des
    betroffenen Kontos ungültig - ein Benutzer muss sich in jedem Fall
    anschließend explizit neu anmelden.
    """
    spalte_links, spalte_mitte, spalte_rechts = st.columns([1, 1.3, 1])

    with spalte_mitte:
        st.write("")
        st.write("")
        komponenten.marke_kopf(gross=True)
        st.write("")

        with st.container(border=True):
            st.markdown("### Neues Passwort festlegen")

            if st.session_state.get("_reset_erfolgreich"):
                st.success("Dein Passwort wurde zurückgesetzt. Du kannst dich jetzt anmelden.")

                if st.button("Zur Anmeldung", key="reset_zur_anmeldung", use_container_width=True):
                    st.session_state.pop("_reset_erfolgreich", None)
                    st.query_params.clear()
                    st.rerun()

                return

            with st.form("passwort_reset_formular"):
                neues_passwort = st.text_input("Neues Passwort", type="password")
                wiederholt = st.text_input("Neues Passwort wiederholen", type="password")
                abgeschickt = st.form_submit_button(
                    "Passwort festlegen", type="primary", use_container_width=True
                )

            if not abgeschickt:
                return

            if neues_passwort != wiederholt:
                st.error("Die Passwörter stimmen nicht überein.")
                return

            erfolg, meldung, benutzer_id = speicher.passwort_reset_einloesen(roher_token, neues_passwort)
            sicherheitslog.protokollieren(
                sicherheitslog.EREIGNIS_PASSWORT_RESET_ABGESCHLOSSEN,
                user_id=benutzer_id,
                erfolgreich=erfolg,
            )

            if erfolg:
                st.session_state["_reset_erfolgreich"] = True
                st.rerun()
            else:
                st.error(meldung)


def authentifizierung_anzeigen():
    """Rendert die vollständige Anmelde-/Registrierungsseite.

    Wird nur aufgerufen, solange niemand angemeldet ist - `web_app.py`
    beendet den Skriptlauf danach per `st.stop()`, sodass die normale
    Clevoriq-Navigation/-Oberfläche gar nicht erst gerendert wird.
    """
    spalte_links, spalte_mitte, spalte_rechts = st.columns([1, 1.3, 1])

    with spalte_mitte:
        st.write("")
        st.write("")
        komponenten.marke_kopf(gross=True)
        komponenten.marke_tagline()
        st.write("")

        # Einmaliger Hinweis nach einer soeben erfolgten, endgültigen
        # Kontolöschung (siehe `abmelden_nach_kontoloeschung`) - `pop`
        # entfernt das Flag sofort wieder, damit es nicht bei einem
        # späteren Login erneut erscheint.
        if st.session_state.pop("_konto_geloescht_hinweis", False):
            st.success("Dein Konto und alle zugehörigen Daten wurden endgültig gelöscht.")

        # Einmaliger Hinweis, wenn diese Sitzung serverseitig beendet
        # wurde (Ablauf/Inaktivität/Widerruf durch eine sicherheitsrelevante
        # Aktion) - siehe `sitzung_gueltig_pruefen`.
        if st.session_state.pop("_sitzung_abgelaufen_hinweis", False):
            st.info("Deine Sitzung ist abgelaufen oder wurde beendet. Bitte melde dich erneut an.")

        with st.container(border=True):
            modus = st.session_state.get(_MODUS_KEY, "login")

            if modus == "register":
                _register_formular()
            elif modus == "passwort_vergessen":
                _passwort_vergessen_formular()
            else:
                _login_formular()


def konto_bereich():
    """Kleiner Konto-/Abmelde-Bereich - wird ans Ende der Sidebar gehängt.

    Rendert nichts, wenn niemand angemeldet ist (sollte in der Praxis
    nicht vorkommen, da die Sidebar nur nach erfolgreichem Login
    überhaupt gezeichnet wird - defensiv trotzdem abgesichert).

    Name als dezente Caption, darunter "Konto & Sicherheit" und
    "Abmelden" linksbündig unter dem Namen (nicht zentriert) und in
    exakt gleicher Breite/Höhe - beide nutzen bewusst
    `use_container_width=True` statt einer natürlichen Textbreite, damit
    sie identisch breit sind UND (Lehre aus dem früheren "melde"-Bug,
    siehe unten) genug Platz für die volle Beschriftung haben, egal wie
    breit der Text im jeweils geladenen Font ausfällt. Die eigentliche
    Linksbündigkeit/Größenangleichung übernimmt der
    `st-key-konto_bereich`-CSS-Hook in `komponenten.py`.

    Icon-Zeichen bewusst als einfache, einfarbige Textsymbole aus dem
    Standard-Unicode-"Arrows"/"Miscellaneous Symbols"-Bereich (⚙ für
    Konto, ↪ für Abmelden) statt als mehrbytiger Farb-Emoji (z. B. das
    vorherige 🚪): Ein Farb-Emoji verlangt eine eigene Emoji-Schriftart
    und wird in ihrer Abwesenheit (oder wenn eine Übersetzungs-/
    Eingabehilfe-Erweiterung des Browsers den Text vor dem Rendern
    anfasst) nicht bloß als Ersatzkästchen, sondern im gemeldeten Fall
    sogar als völlig anderer, falscher Text ("Sündigen" statt "Abmelden")
    dargestellt - ein einfaches BMP-Textsymbol wird dagegen von jeder
    Standard-Systemschrift (auch dem Inter-Fallback) wie gewöhnlicher
    Text gerendert und ist von genau dieser Klasse Ersetzungsfehler nicht
    betroffen.
    """
    benutzer = aktueller_benutzer()

    if not benutzer:
        return

    st.divider()

    with st.container(key="konto_bereich"):
        st.caption(f"👤 {benutzer['benutzername']}")

        if st.button(BEREICH_KONTO, key="konto_seite_button", use_container_width=True):
            st.session_state.aktiver_bereich = BEREICH_KONTO
            st.rerun()

        if st.button("↪ Abmelden", key="abmelden_button", use_container_width=True):
            abmelden()


def verifizierung_seite(benutzer_id):
    """Rendert den eingeschränkten "E-Mail bestätigen"-Bereich für noch
    nicht verifizierte Konten (siehe `web_app.py`s Zugriffsbeschränkung:
    `BEREICH_VERIFIZIERUNG` ist einer von nur zwei Bereichen, die ein
    unbestätigtes Konto überhaupt erreichen kann, neben Konto &
    Sicherheit und Abmelden - keine Dokumente, kein Chat, keine
    KI-Funktionen).
    """
    komponenten.seiten_hero(
        "✉️",
        "E-Mail-Adresse bestätigen",
        "Bitte bestätige deine E-Mail-Adresse, um Clevoriq Documents vollständig nutzen zu können.",
    )

    konto = speicher.benutzer_konto_daten(benutzer_id)

    if not konto:
        st.error("Konto nicht gefunden.")
        return

    meldung = st.session_state.pop("_verifizierung_meldung", None)

    if meldung:
        (st.success if meldung[0] else st.error)(meldung[1])

    st.info(
        f"Wir haben eine Bestätigungs-E-Mail an **{konto['email']}** gesendet. "
        "Bitte klicke auf den Link in dieser E-Mail, um dein Konto vollständig freizuschalten."
    )
    st.caption(
        "Bis zur Bestätigung kannst du dich anmelden und dein Konto verwalten, "
        "aber noch keine Dokumente hochladen oder die KI-Funktionen nutzen."
    )

    aktiv, wartezeit = ratenbegrenzung.resend_cooldown_aktiv(konto["email"])

    if st.button(
        "Bestätigungs-E-Mail erneut senden",
        key="verifizierung_erneut_senden",
        disabled=aktiv,
        use_container_width=True,
    ):
        erfolg, text = bestaetigungsmail_anfordern(benutzer_id, konto["email"])
        st.session_state["_verifizierung_meldung"] = (erfolg, text)
        st.rerun()

    if aktiv:
        st.caption(f"Erneutes Senden möglich in {_wartezeit_text(wartezeit)}.")
