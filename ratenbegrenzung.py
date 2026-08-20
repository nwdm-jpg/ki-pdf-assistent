"""Persistentes Rate-Limiting für Authentifizierungs-Aktionen.

Bewusst NICHT ein reines In-Memory-Dict (das würde weder einen
App-Neustart noch mehrere Streamlit-Worker-Prozesse überstehen) -
jeder Versuch wird als Ereignis in `security_events` (siehe
`speicher.py`) gespeichert und über ein gleitendes Zeitfenster
ausgewertet.

Geschlüsselt wird über die NORMALISIERTE Identität (E-Mail oder
Benutzername, klein geschrieben/getrimmt) - bewusst UNABHÄNGIG davon,
ob zu dieser Identität überhaupt ein Konto existiert: `pruefen()` und
`versuch_aufzeichnen()` verhalten sich für existierende wie für nicht
existierende Konten identisch, damit sich aus dem Zeitverhalten oder
den Fehlermeldungen keine Existenz eines Kontos ableiten lässt (siehe
CLAUDE.md "keine Account Enumeration"). Die Client-IP wird zusätzlich,
soweit von Streamlit überhaupt zuverlässig verfügbar, mitprotokolliert
(rein informativ fürs Audit-Log) - siehe `_client_ip()` für die
bekannten Grenzen dieser Erkennung.

Gewählte Limits (siehe `_LIMITS`) sind bewusst moderat: genug Toleranz
für normale Tippfehler, aber eine spürbare Bremse gegen automatisiertes
Durchprobieren. Bei wiederholtem Auslösen INNERHALB eines längeren
Beobachtungsfensters (24 Stunden) verdoppelt sich die Sperrzeit pro
weiterer Sperre (bis zu einer Obergrenze von 4 Stunden) - ein
anhaltender Angriff kann die Sperre so nicht einfach durch Abwarten der
Basis-Sperrzeit umgehen, ohne dass ein einzelner Ausrutscher eines
normalen Nutzers zu einer unverhältnismäßig langen Sperre führt.
"""

from datetime import datetime, timedelta

import speicher


# aktion: (max_versuche, fenster_minuten, basis_sperre_minuten, nur_fehlgeschlagene_zaehlen)
#
# - login: nur fehlgeschlagene Versuche zählen (ein erfolgreicher Login
#   soll nie zu einer Sperre beitragen).
# - register/password_reset_request/resend_verification: JEDE Anfrage
#   zählt (unabhängig vom Ergebnis) - das sind die eigentlichen
#   Missbrauchs-/Spam-Flächen (E-Mail-Flut, Enumeration-Versuche).
# - email_verification_attempt: nur fehlgeschlagene (ungültige/abgelaufene)
#   Tokens zählen - ein gültiger Link soll nie limitiert werden.
_LIMITS = {
    "login": (5, 15, 15, True),
    "register": (5, 60, 30, False),
    "password_reset_request": (5, 60, 30, False),
    "resend_verification": (3, 15, 15, False),
    "email_verification_attempt": (10, 15, 30, True),
}

# Eigener, kurzer Mindestabstand zwischen zwei "Bestätigungs-E-Mail
# erneut senden"-Anfragen derselben Identität - unabhängig vom größeren
# Fenster-Limit oben, verhindert v. a. mehrfaches Klicken in kurzer Folge.
RESEND_COOLDOWN_SEKUNDEN = 60

_ESKALATIONS_FENSTER_STUNDEN = 24
_ESKALATIONS_OBERGRENZE_MINUTEN = 240


def _normalisiert(identitaet):
    return (identitaet or "").strip().lower()


def client_ip():
    """Bestes-Wissen-Ermittlung der Client-IP für das Audit-Log.

    BEKANNTE GRENZE: Streamlit stellt keine garantiert zuverlässige,
    proxy-sichere Client-IP-Erkennung bereit - `st.context.headers`
    liefert (falls überhaupt vorhanden) rohe HTTP-Header, die hinter
    einem Reverse-Proxy/Load-Balancer frei fälschbar sind, sofern dieser
    sie nicht selbst kontrolliert überschreibt. Diese Funktion ist daher
    bewusst nur eine Best-Effort-Ergänzung fürs Audit-Log, NIE die
    alleinige Grundlage einer Sicherheitsentscheidung (das Rate-Limiting
    selbst basiert primär auf der Identität, nicht der IP). Liefert
    `None`, wenn keine Information verfügbar ist - Aufrufer müssen das
    handhaben können.
    """
    try:
        import streamlit as st

        headers = st.context.headers
        weitergeleitet = headers.get("X-Forwarded-For") if headers else None

        if weitergeleitet:
            return weitergeleitet.split(",")[0].strip() or None
    except Exception:
        return None

    return None


def pruefen(aktion, identitaet):
    """Prüft, ob `aktion` für diese Identität aktuell erlaubt ist.

    Gibt `(erlaubt: bool, wartezeit_sekunden: int)` zurück. Zählt
    ausschließlich bereits über `versuch_aufzeichnen` protokollierte
    Versuche der letzten `fenster_minuten` - ruft selbst KEINE
    Protokollierung auf (siehe `versuch_aufzeichnen`), damit ein reiner
    "darf ich?"-Check keine Nebenwirkung hat.
    """
    max_versuche, fenster_minuten, basis_sperre_minuten, nur_fehlgeschlagen = _LIMITS[aktion]
    identitaet = _normalisiert(identitaet)
    seit = (datetime.now() - timedelta(minutes=fenster_minuten)).isoformat(timespec="seconds")

    anzahl = speicher.sicherheitsereignisse_zaehlen(
        f"{aktion}_versuch", identitaet, seit, nur_fehlgeschlagen=nur_fehlgeschlagen
    )

    if anzahl < max_versuche:
        return True, 0

    effektive_sperre_minuten = _eskalierte_sperre_minuten(identitaet, basis_sperre_minuten)
    letzter = speicher.letztes_ereignis_zeitpunkt(f"{aktion}_versuch", identitaet)

    if not letzter:
        return True, 0

    sperre_bis = datetime.fromisoformat(letzter) + timedelta(minutes=effektive_sperre_minuten)
    verbleibend_sekunden = (sperre_bis - datetime.now()).total_seconds()

    if verbleibend_sekunden <= 0:
        return True, 0

    _sperr_ereignis_aufzeichnen_falls_neu(aktion, identitaet, basis_sperre_minuten)

    return False, int(verbleibend_sekunden)


def _eskalierte_sperre_minuten(identitaet, basis_sperre_minuten):
    eskalations_seit = (
        datetime.now() - timedelta(hours=_ESKALATIONS_FENSTER_STUNDEN)
    ).isoformat(timespec="seconds")
    anzahl_sperren = speicher.sicherheitsereignisse_zaehlen(
        "rate_limit_triggered", identitaet, eskalations_seit, nur_fehlgeschlagen=False
    )
    faktor = min(2 ** anzahl_sperren, 16)
    return min(basis_sperre_minuten * faktor, _ESKALATIONS_OBERGRENZE_MINUTEN)


def _sperr_ereignis_aufzeichnen_falls_neu(aktion, identitaet, basis_sperre_minuten):
    """Protokolliert eine ausgelöste Sperre höchstens einmal je Basis-Sperrfenster.

    Ohne diese Deduplizierung würde jeder weitere Versuch WÄHREND einer
    bereits aktiven Sperre selbst als neue "Sperre ausgelöst"-Episode
    zählen und die Eskalation künstlich aufblähen - ein normaler Nutzer,
    der die Fehlermeldung ignoriert und mehrfach erneut klickt, würde
    dadurch unverhältnismäßig lange gesperrt (siehe Anforderung "normale
    Benutzer dürfen nicht bei einzelnen Tippfehlern unnötig lange
    ausgesperrt werden").
    """
    letzter = speicher.letztes_ereignis_zeitpunkt("rate_limit_triggered", identitaet)

    if letzter and datetime.fromisoformat(letzter) + timedelta(minutes=basis_sperre_minuten) > datetime.now():
        return

    speicher.sicherheitsereignis_speichern(
        "rate_limit_triggered", None, identitaet, client_ip(), False, aktion
    )


def versuch_aufzeichnen(aktion, identitaet, erfolgreich):
    """Protokolliert einen tatsächlich durchgeführten Versuch dieser
    Aktion - MUSS nach jedem echten Versuch aufgerufen werden (nicht nur
    bei `pruefen()`), sonst zählt das Fenster nicht korrekt."""
    identitaet = _normalisiert(identitaet)
    speicher.sicherheitsereignis_speichern(
        f"{aktion}_versuch", None, identitaet, client_ip(), erfolgreich, None
    )


def resend_cooldown_aktiv(identitaet):
    """Kurzer Mindestabstand zwischen zwei Anfragen zum erneuten Senden
    der Bestätigungs-E-Mail, unabhängig vom größeren Fenster-Limit.
    Gibt `(aktiv: bool, wartezeit_sekunden: int)` zurück."""
    identitaet = _normalisiert(identitaet)
    letzter = speicher.letztes_ereignis_zeitpunkt("resend_verification_versuch", identitaet)

    if not letzter:
        return False, 0

    naechste_erlaubt = datetime.fromisoformat(letzter) + timedelta(seconds=RESEND_COOLDOWN_SEKUNDEN)
    verbleibend = (naechste_erlaubt - datetime.now()).total_seconds()

    return verbleibend > 0, max(0, int(verbleibend))


def wartezeit_text(sekunden):
    """Deutsche, grob gerundete Wartezeit-Formatierung für Fehlermeldungen."""
    if sekunden < 60:
        return f"{max(1, sekunden)} Sekunden"

    minuten = max(1, round(sekunden / 60))
    return f"{minuten} Minute{'n' if minuten != 1 else ''}"
