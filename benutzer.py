"""Streamlit-Anmelde-/Registrierungs-Oberfläche und Sitzungsverwaltung.

Trennt die Authentifizierungs-UI und den Sitzungszustand ("wer ist
aktuell angemeldet") von der reinen Krypto-/Validierungslogik
(`auth.py`) und der Datenbank-Persistenz (`speicher.py`) - `web_app.py`
ruft nur die Funktionen dieses Moduls auf (`ist_angemeldet`,
`authentifizierung_anzeigen`, `aktueller_benutzer_id`, `konto_bereich`,
`abmelden`) und enthält selbst keine Login-/Registrierungs-Details.

Sitzung: rein `st.session_state`-basiert, kein Cookie/Token und keine
serverseitige Sitzungstabelle - ein neuer Browser-Tab bzw. eine neue
Streamlit-Sitzung verlangt immer erneut eine Anmeldung. Das ist bewusst
so gewählt statt einer selbstgebauten Cookie-Notlösung (siehe
Aufgabenstellung); ein "angemeldet bleiben"-Mechanismus kann später
sauber ergänzt werden, ohne an diesem Modul grundlegend etwas ändern zu
müssen.

Wiederverwendet für die Login-/Registrierungsseite bewusst dieselben
Bausteine wie der Rest von AVENLOQ (`komponenten.marke_kopf`,
`komponenten.marke_tagline`, `st.form_submit_button(type="primary")` für
den Farbverlauf-Button, `st.container(border=True)` für die Karte) -
keine eigene Farb-/Typografie-Definition, damit die Optik automatisch
konsistent bleibt und nicht zweimal gepflegt werden muss.
"""

import streamlit as st

import auth
import komponenten
import speicher


_SESSION_KEY = "benutzer"
_MODUS_KEY = "auth_modus"


def ist_angemeldet():
    return st.session_state.get(_SESSION_KEY) is not None


def aktueller_benutzer():
    """Gibt {"id", "benutzername", "email"} des angemeldeten Benutzers zurück (oder None)."""
    return st.session_state.get(_SESSION_KEY)


def aktueller_benutzer_id():
    benutzer = aktueller_benutzer()
    return benutzer["id"] if benutzer else None


def abmelden():
    """Beendet die Sitzung vollständig und kehrt zum Anmeldebildschirm zurück.

    Bewusst ein kompletter `st.session_state.clear()` statt einzelner
    Schlüssel: Die App verteilt Sitzungszustand über viele Keys
    (aktueller Chat, Dokumentauswahl je Bereich, Analyse-/
    Prüfungsergebnisse, Formular-Widget-States) - nur ein vollständiges
    Zurücksetzen garantiert, dass nach einer Abmeldung (bzw. Anmeldung
    als anderer Benutzer) nichts vom vorherigen Benutzer sichtbar
    bleibt.
    """
    st.session_state.clear()
    st.rerun()


def abmelden_nach_kontoloeschung():
    """Wie `abmelden()`, aber nach einer endgültigen Kontolöschung
    (`konto.py`) - derselbe vollständige `st.session_state.clear()`, damit
    keinerlei Zustand des gelöschten Kontos zurückbleibt, zusätzlich mit
    einem einmaligen Hinweis-Flag, das `authentifizierung_anzeigen()`
    direkt nach dem Zurücksetzen einmalig anzeigt und danach selbst
    wieder entfernt (`st.session_state.pop`).
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
        st.session_state[_SESSION_KEY] = {
            "id": benutzer["id"],
            "benutzername": benutzer["benutzername"],
            "email": benutzer["email"],
        }
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

    st.session_state[_SESSION_KEY] = {
        "id": neue_id,
        "benutzername": benutzername,
        "email": email.lower(),
    }
    # Registrierung meldet direkt an (siehe unten) - zählt für die
    # Aktivitäts-Nachverfolgung als erster Login.
    speicher.letzten_login_aktualisieren(neue_id)
    return []


def _login_formular():
    st.markdown("### Willkommen zurück")
    st.caption("Mit deinem AVENLOQ-Konto anmelden.")

    with st.form("login_formular"):
        login_wert = st.text_input("E-Mail oder Benutzername")
        passwort = st.text_input("Passwort", type="password")
        abgeschickt = st.form_submit_button(
            "Anmelden", type="primary", use_container_width=True
        )

    if abgeschickt:
        if not login_wert.strip() or not passwort:
            st.error("Bitte E-Mail/Benutzername und Passwort eingeben.")
        elif _login_versuchen(login_wert, passwort):
            st.rerun()
        else:
            # Bewusst unspezifisch - verrät nicht, ob der Benutzername/die
            # E-Mail überhaupt existiert.
            st.error("Benutzername/E-Mail oder Passwort ist falsch.")

    if st.button("Noch kein Konto? Registrieren", key="zu_registrieren", use_container_width=True):
        st.session_state[_MODUS_KEY] = "register"
        st.rerun()


def _register_formular():
    st.markdown("### Konto erstellen")
    st.caption("Lege dein AVENLOQ-Konto an.")

    with st.form("register_formular"):
        benutzername = st.text_input("Benutzername")
        email = st.text_input("E-Mail")
        passwort = st.text_input("Passwort", type="password")
        passwort_wiederholen = st.text_input("Passwort wiederholen", type="password")
        abgeschickt = st.form_submit_button(
            "Konto erstellen", type="primary", use_container_width=True
        )

    if abgeschickt:
        fehler = _registrieren(benutzername, email, passwort, passwort_wiederholen)

        if fehler:
            for meldung in fehler:
                st.error(meldung)
        else:
            st.rerun()

    if st.button("Bereits registriert? Anmelden", key="zu_login", use_container_width=True):
        st.session_state[_MODUS_KEY] = "login"
        st.rerun()


def authentifizierung_anzeigen():
    """Rendert die vollständige Anmelde-/Registrierungsseite.

    Wird nur aufgerufen, solange niemand angemeldet ist - `web_app.py`
    beendet den Skriptlauf danach per `st.stop()`, sodass die normale
    AVENLOQ-Navigation/-Oberfläche gar nicht erst gerendert wird.
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

        with st.container(border=True):
            modus = st.session_state.get(_MODUS_KEY, "login")

            if modus == "register":
                _register_formular()
            else:
                _login_formular()


BEREICH_KONTO = "⚙️ Konto & Sicherheit"


def konto_bereich():
    """Kleiner Konto-/Abmelde-Bereich - wird ans Ende der Sidebar gehängt.

    Rendert nichts, wenn niemand angemeldet ist (sollte in der Praxis
    nicht vorkommen, da die Sidebar nur nach erfolgreichem Login
    überhaupt gezeichnet wird - defensiv trotzdem abgesichert).

    Kompakt und zentriert (Name als dezente Caption, darunter die kleine
    "Konto & Sicherheit"-Einstiegsschaltfläche und "Abmelden" - beide als
    neutrale, kompakte Sekundär-Buttons in ihrer natürlichen Textbreite,
    NICHT in eine schmale `st.columns`-Spalte gezwängt). Die Zentrierung
    übernimmt der `st-key-konto_bereich`-CSS-Hook in `komponenten.py`
    über Flexbox auf dem Container selbst (`align-items: center`) statt
    über feste Spaltenbreiten-Verhältnisse: Eine feste Spaltenbreite ist
    nur so breit wie für die Schriftart getestet, mit der sie gebaut
    wurde - lädt die Inter-Schrift im Browser nicht (z. B. blockiertes
    Google-Fonts-CDN in einem abgeschotteten Netzwerk), rendert der
    Fallback-Systemfont "Abmelden" breiter, Streamlits Standard-Button-
    Stil bricht Beschriftungen nicht um (`white-space: nowrap`) und
    schneidet sie bei zu wenig Platz sichtbar ab - genau das erzeugte den
    gemeldeten "melde"-Darstellungsfehler (Mitte von "Abmelden" bleibt
    sichtbar, beide Enden werden abgeschnitten). Mit natürlicher
    Button-Breite kann das nicht mehr passieren, unabhängig von Schriftart
    oder Zoomstufe.
    """
    benutzer = aktueller_benutzer()

    if not benutzer:
        return

    st.divider()

    with st.container(key="konto_bereich"):
        st.caption(f"👤 {benutzer['benutzername']}")

        if st.button(BEREICH_KONTO, key="konto_seite_button"):
            st.session_state.aktiver_bereich = BEREICH_KONTO
            st.rerun()

        if st.button("Abmelden", key="abmelden_button"):
            abmelden()
