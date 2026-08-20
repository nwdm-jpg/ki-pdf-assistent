"""Abstraktion für transaktionale E-Mails (Verifizierung, Passwort-Reset,
Sicherheits-Benachrichtigungen) mit optionalem Resend-Produktivanbieter.

Architektur: `_Anbieter` ist die Schnittstelle, gegen die der Rest der App
programmiert (`versenden`). `_EntwicklungsAnbieter` (Standard, siehe
`_anbieter_waehlen`) versendet nichts wirklich - loggt die Nachricht nach
stdout und hält sie zusätzlich im Prozessspeicher vor, damit lokale Tests/
die UI im Entwicklungsmodus den Inhalt direkt einsehen können, ohne echten
Versand und ohne Netzwerkzugriff. `_ResendAnbieter` versendet echte
E-Mails über die Resend-HTTP-API, sobald `CLEVORIQ_EMAIL_PROVIDER=resend`
gesetzt ist - siehe dessen Docstring für die benötigte Konfiguration.

WICHTIG: Es werden NIEMALS Zugangsdaten/API-Keys hier im Code hinterlegt.
Der Produktiv-Anbieter liest sein Secret ausschließlich aus der
Umgebungsvariable `RESEND_API_KEY` - nie aus einem Literal im Quellcode,
nie geloggt (auch nicht in Fehlermeldungen, siehe `_ResendAnbieter.versenden`).
"""

import json
import os
import urllib.error
import urllib.request


# Steuert, welcher Anbieter genutzt wird - Standard "dev" (kein echter
# Versand, siehe `_EntwicklungsAnbieter`). Produktivbetrieb: "resend".
# `_ANBIETER_ENV_VAR_ALT` ist der historische Name aus der Zeit vor dem
# Clevoriq-Rebrand - wird weiterhin als Fallback gelesen, damit eine
# bereits gesetzte Umgebungsvariable durch die Umbenennung nicht
# stillschweigend wirkungslos wird.
_ANBIETER_ENV_VAR = "CLEVORIQ_EMAIL_PROVIDER"
_ANBIETER_ENV_VAR_ALT = "AVENLOQ_EMAIL_PROVIDER"

# Absenderadresse (z. B. "Clevoriq <noreply@notify.clevoriq.de>") und
# optionale Reply-To-Adresse (z. B. "info@clevoriq.de") - beide NUR aus
# der Umgebung, nie im Code vorgegeben: die tatsächliche Absenderdomain
# hängt von einer bei Resend separat durchzuführenden Domain-Verifizierung
# ab, die hier bewusst nicht simuliert wird.
_EMAIL_FROM_ENV_VAR = "CLEVORIQ_EMAIL_FROM"
_EMAIL_REPLY_TO_ENV_VAR = "CLEVORIQ_EMAIL_REPLY_TO"
_RESEND_API_KEY_ENV_VAR = "RESEND_API_KEY"

# Basis-URL der laufenden App, für Verifizierungs-/Reset-Links in
# versendeten E-Mails (siehe `verifizierungs_link`/`reset_link`). Fällt
# auf die lokale Standard-Adresse zurück, wenn nicht gesetzt - sinnvoll
# für lokale Entwicklung, MUSS in einer echten Bereitstellung gesetzt sein.
_APP_BASE_URL_ENV_VAR = "CLEVORIQ_APP_BASE_URL"
_STANDARD_BASIS_URL = "http://localhost:8501"

# Im Entwicklungsmodus "gesendete" Nachrichten - rein informativ für lokale
# Tests/Diagnose (z. B. eine Skill-/Testsuite, die den zuletzt erzeugten
# Verifizierungs-/Reset-Link prüfen will), NICHT persistent, NICHT für
# Produktivbetrieb gedacht.
GESENDETE_ENTWICKLUNGS_MAILS = []


class _Anbieter:
    """Schnittstelle für einen E-Mail-Anbieter. Nie direkt verwenden -
    siehe `versenden()` unten für den öffentlichen Einstiegspunkt."""

    def versenden(self, empfaenger, betreff, text):
        raise NotImplementedError


class _EntwicklungsAnbieter(_Anbieter):
    """Versendet nichts wirklich - loggt die Nachricht nur nach stdout und
    merkt sie sich im Prozessspeicher. Aktiv, solange kein echter Anbieter
    konfiguriert ist (siehe Modul-Docstring). Explizit KEIN Fake, der einen
    erfolgreichen Versand vortäuscht: Aufrufer bekommen den Klartext-Inhalt
    zurück und können ihn (z. B. bei der E-Mail-Verifizierung) direkt in
    der UI als "Entwicklungsmodus"-Hinweis anzeigen, statt so zu tun, als
    sei tatsächlich eine E-Mail zugestellt worden.
    """

    def versenden(self, empfaenger, betreff, text):
        eintrag = {"empfaenger": empfaenger, "betreff": betreff, "text": text}
        GESENDETE_ENTWICKLUNGS_MAILS.append(eintrag)

        print(
            "[E-Mail-Versand: ENTWICKLUNGSMODUS - kein echter Anbieter konfiguriert]\n"
            f"  An: {empfaenger}\n"
            f"  Betreff: {betreff}\n"
            f"  Inhalt:\n{text}\n"
        )
        return eintrag


class _ResendAnbieter(_Anbieter):
    """Versendet echte E-Mails über die Resend-HTTP-API (api.resend.com).

    Aktiv, sobald `CLEVORIQ_EMAIL_PROVIDER=resend` gesetzt ist. Benötigt
    zwingend `RESEND_API_KEY` (Secret) und `CLEVORIQ_EMAIL_FROM`
    (Absenderadresse, z. B. "Clevoriq <noreply@notify.clevoriq.de>") als
    Umgebungsvariablen; `CLEVORIQ_EMAIL_REPLY_TO` (z. B. "info@clevoriq.de")
    ist optional. Fehlt eine der Pflichtvariablen, wird sofort ein klarer
    Fehler geworfen - siehe Modul-Docstring "In Produktion muss das System
    bei fehlender E-Mail-Konfiguration klar und sicher fehlschlagen statt
    so zu tun, als wäre eine Mail verschickt worden".

    Die konkrete Absenderdomain muss VORHER separat bei Resend verifiziert
    werden (DNS-Einträge) - das ist außerhalb dieser App und wird hier
    nicht simuliert oder vorausgesetzt; ein Versand mit einer nicht
    verifizierten Domain schlägt bei Resend selbst fehl (HTTPError unten).

    Nutzt bewusst nur die Python-Standardbibliothek (`urllib`) statt einer
    zusätzlichen Abhängigkeit (z. B. dem `resend`-Paket oder `requests`) -
    die Resend-REST-API ist mit einem einzigen POST-Request vollständig
    nutzbar, eine weitere Abhängigkeit wäre für dieses kleine Projekt
    unverhältnismäßig.
    """

    def __init__(self):
        self._api_key = os.environ.get(_RESEND_API_KEY_ENV_VAR, "").strip()
        self._absender = os.environ.get(_EMAIL_FROM_ENV_VAR, "").strip()
        self._reply_to = os.environ.get(_EMAIL_REPLY_TO_ENV_VAR, "").strip() or None

        fehlende_variablen = [
            name
            for name, wert in (
                (_RESEND_API_KEY_ENV_VAR, self._api_key),
                (_EMAIL_FROM_ENV_VAR, self._absender),
            )
            if not wert
        ]

        if fehlende_variablen:
            raise RuntimeError(
                "E-Mail-Anbieter 'resend' ist aktiviert, aber die "
                f"Umgebungsvariable(n) {', '.join(fehlende_variablen)} "
                "fehlen. E-Mail-Versand wird deshalb NICHT durchgeführt, "
                "statt fälschlich einen erfolgreichen Versand vorzutäuschen."
            )

    def versenden(self, empfaenger, betreff, text):
        payload = {
            "from": self._absender,
            "to": [empfaenger],
            "subject": betreff,
            "text": text,
        }

        if self._reply_to:
            payload["reply_to"] = self._reply_to

        anfrage = urllib.request.Request(
            "https://api.resend.com/emails",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(anfrage, timeout=10) as antwort:
                antwort.read()
        except urllib.error.HTTPError as fehler:
            # Bewusst NUR Status/Grund von Resend selbst in der
            # Fehlermeldung - niemals den API-Key oder den vollständigen
            # Anfrage-Body (der die Empfänger-/Absenderadresse enthält).
            raise RuntimeError(
                f"E-Mail-Versand über Resend fehlgeschlagen (HTTP {fehler.code})."
            ) from None
        except urllib.error.URLError:
            raise RuntimeError(
                "E-Mail-Versand über Resend fehlgeschlagen (Netzwerkfehler)."
            ) from None

        return {"empfaenger": empfaenger, "betreff": betreff}


def _anbieter_waehlen():
    """Wählt den aktiven Anbieter anhand von `CLEVORIQ_EMAIL_PROVIDER`
    (Fallback: der historische Name `AVENLOQ_EMAIL_PROVIDER`).

    Ein unbekannter oder ein absichtlich noch nicht angebundener Wert
    führt zu einer klaren Fehlermeldung statt eines stillen Fallbacks,
    damit ein Konfigurationsfehler in Produktion nicht unbemerkt bliebe.
    Automatisierte Tests dürfen diese Variable NIE auf "resend" setzen -
    der Standardwert "dev" versendet nie echte E-Mails.
    """
    name = (
        os.environ.get(_ANBIETER_ENV_VAR)
        or os.environ.get(_ANBIETER_ENV_VAR_ALT)
        or "dev"
    ).strip().lower()

    if name in ("dev", "development", ""):
        return _EntwicklungsAnbieter()

    if name == "resend":
        return _ResendAnbieter()

    if name in ("postmark", "ses", "amazon-ses"):
        raise NotImplementedError(
            f"E-Mail-Anbieter '{name}' ist vorbereitet, aber noch nicht "
            "angebunden. Implementiere `_Anbieter.versenden` für diesen "
            "Anbieter und lies sein API-Secret ausschließlich aus einer "
            "Umgebungsvariable, nie aus dem Quellcode."
        )

    raise ValueError(f"Unbekannter E-Mail-Anbieter: '{name}'")


def basis_url():
    """Basis-URL der laufenden App (ohne abschließenden Schrägstrich)."""
    return (os.environ.get(_APP_BASE_URL_ENV_VAR) or _STANDARD_BASIS_URL).rstrip("/")


def verifizierungs_link(token):
    """Baut den in einer Verifizierungs-E-Mail versendeten Link. Der
    rohe Token landet dabei zwangsläufig in der E-Mail und - nach dem
    Klick - kurzzeitig in der Browser-URL; `web_app.py` entfernt ihn dort
    unmittelbar nach der Verarbeitung wieder (siehe dortige Verarbeitung
    von `st.query_params`)."""
    return f"{basis_url()}/?verify_token={token}"


def reset_link(token):
    """Baut den in einer Passwort-Reset-E-Mail versendeten Link - siehe
    `verifizierungs_link` für die Handhabung des Tokens in der URL."""
    return f"{basis_url()}/?reset_token={token}"


def versenden(empfaenger, betreff, text):
    """Einziger öffentlicher Einstiegspunkt zum Versenden einer E-Mail.

    Reine, anbieter-unabhängige Fassade - der Rest der App ruft nur diese
    Funktion auf, nie einen konkreten `_Anbieter` direkt.
    """
    return _anbieter_waehlen().versenden(empfaenger, betreff, text)


# --- Vorbereitete transaktionale Nachrichten -------------------------------
#
# Jede Funktion baut nur Betreff/Text zusammen und ruft `versenden()` -
# reine Vorbereitung für Abschnitt 11/18 der Aufgabenstellung. Enthalten
# werden bewusst NIE Passwörter oder andere sensible Geheimnisse selbst,
# nur Links/Hinweise. Diese Funktionen sind aktuell UNGENUTZT von der
# Kern-Logik (kein Produktiv-Versand angebunden) - sie liegen bereit, damit
# `konto.py` sie an geeigneter Stelle einhängen kann, sobald ein Anbieter
# aktiv ist.


def sende_registrierung_verifizierung(empfaenger, verifizierungs_link):
    return versenden(
        empfaenger,
        "Bestätige deine E-Mail-Adresse bei Clevoriq",
        f"Willkommen bei Clevoriq!\n\nBitte bestätige deine E-Mail-Adresse:\n{verifizierungs_link}\n",
    )


def sende_email_geaendert(empfaenger, verifizierungs_link):
    return versenden(
        empfaenger,
        "Deine E-Mail-Adresse bei Clevoriq wurde geändert",
        (
            "Die E-Mail-Adresse deines Clevoriq-Kontos wurde geändert.\n\n"
            f"Bitte bestätige die neue Adresse:\n{verifizierungs_link}\n\n"
            "Warst du das nicht, wende dich umgehend an den Support."
        ),
    )


def sende_passwort_geaendert(empfaenger):
    return versenden(
        empfaenger,
        "Dein Clevoriq-Passwort wurde geändert",
        (
            "Das Passwort deines Clevoriq-Kontos wurde soeben geändert.\n\n"
            "Warst du das nicht, wende dich umgehend an den Support."
        ),
    )


def sende_passwort_reset(empfaenger, reset_link):
    return versenden(
        empfaenger,
        "Passwort zurücksetzen bei Clevoriq",
        (
            "Für dein Clevoriq-Konto wurde ein Passwort-Reset angefordert.\n\n"
            f"Link zum Zurücksetzen (zeitlich begrenzt gültig):\n{reset_link}\n\n"
            "Hast du das nicht angefordert, kannst du diese Nachricht ignorieren."
        ),
    )


def sende_konto_geloescht(empfaenger):
    return versenden(
        empfaenger,
        "Dein Clevoriq-Konto wurde gelöscht",
        (
            "Dein Clevoriq-Konto und alle zugehörigen Daten wurden "
            "unwiderruflich gelöscht."
        ),
    )
