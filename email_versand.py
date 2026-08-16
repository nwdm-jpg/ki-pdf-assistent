"""Abstraktion für transaktionale E-Mails (Verifizierung, Passwort-Reset,
Sicherheits-Benachrichtigungen) - noch OHNE angebundenen Produktiv-Anbieter.

Architektur: `_Anbieter` ist die Schnittstelle, gegen die der Rest der App
programmiert (`versenden`). Aktuell existiert nur `_EntwicklungsAnbieter`
(loggt die "gesendete" Nachricht nach stdout und hält sie zusätzlich im
Prozessspeicher vor, damit die UI im Entwicklungsmodus einen Link direkt
anzeigen kann, ohne echten Versand). Ein späterer Produktiv-Anbieter
(Resend/Postmark/Amazon SES) müsste nur `_Anbieter.versenden` implementieren
und in `_anbieter_waehlen` ergänzt werden - keine der aufrufenden
Konto-/Sicherheitsfunktionen (`speicher.py`, `konto.py`, `benutzer.py`)
müsste sich dafür ändern.

WICHTIG: Es werden absichtlich KEINE Zugangsdaten/API-Keys hier im Code
hinterlegt. Ein künftiger Produktiv-Anbieter liest sein Secret
ausschließlich aus einer Umgebungsvariable (z. B. `RESEND_API_KEY`), nie
aus einem Literal im Quellcode.
"""

import os


# Steuert, welcher Anbieter genutzt wird - Standard "dev" (kein echter
# Versand). Ein Produktiv-Anbieter würde über diese Variable aktiviert,
# z. B. AVENLOQ_EMAIL_PROVIDER=resend.
_ANBIETER_ENV_VAR = "AVENLOQ_EMAIL_PROVIDER"

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


def _anbieter_waehlen():
    """Wählt den aktiven Anbieter anhand von `AVENLOQ_EMAIL_PROVIDER`.

    Produktiv-Anbieter sind bewusst NICHT implementiert (siehe
    Aufgabenstellung: "Do NOT connect a production provider yet") - ein
    unbekannter oder ein absichtlich noch nicht angebundener Wert führt zu
    einer klaren Fehlermeldung statt eines stillen Fallbacks, damit ein
    Konfigurationsfehler in Produktion nicht unbemerkt bliebe.
    """
    name = (os.environ.get(_ANBIETER_ENV_VAR) or "dev").strip().lower()

    if name in ("dev", "development", ""):
        return _EntwicklungsAnbieter()

    if name in ("resend", "postmark", "ses", "amazon-ses"):
        raise NotImplementedError(
            f"E-Mail-Anbieter '{name}' ist vorbereitet, aber noch nicht "
            "angebunden. Implementiere `_Anbieter.versenden` für diesen "
            "Anbieter und lies sein API-Secret ausschließlich aus einer "
            "Umgebungsvariable, nie aus dem Quellcode."
        )

    raise ValueError(f"Unbekannter E-Mail-Anbieter: '{name}'")


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
        "Bestätige deine E-Mail-Adresse bei AVENLOQ",
        f"Willkommen bei AVENLOQ!\n\nBitte bestätige deine E-Mail-Adresse:\n{verifizierungs_link}\n",
    )


def sende_email_geaendert(empfaenger, verifizierungs_link):
    return versenden(
        empfaenger,
        "Deine E-Mail-Adresse bei AVENLOQ wurde geändert",
        (
            "Die E-Mail-Adresse deines AVENLOQ-Kontos wurde geändert.\n\n"
            f"Bitte bestätige die neue Adresse:\n{verifizierungs_link}\n\n"
            "Warst du das nicht, wende dich umgehend an den Support."
        ),
    )


def sende_passwort_geaendert(empfaenger):
    return versenden(
        empfaenger,
        "Dein AVENLOQ-Passwort wurde geändert",
        (
            "Das Passwort deines AVENLOQ-Kontos wurde soeben geändert.\n\n"
            "Warst du das nicht, wende dich umgehend an den Support."
        ),
    )


def sende_passwort_reset(empfaenger, reset_link):
    return versenden(
        empfaenger,
        "Passwort zurücksetzen bei AVENLOQ",
        (
            "Für dein AVENLOQ-Konto wurde ein Passwort-Reset angefordert.\n\n"
            f"Link zum Zurücksetzen (zeitlich begrenzt gültig):\n{reset_link}\n\n"
            "Hast du das nicht angefordert, kannst du diese Nachricht ignorieren."
        ),
    )


def sende_konto_geloescht(empfaenger):
    return versenden(
        empfaenger,
        "Dein AVENLOQ-Konto wurde gelöscht",
        (
            "Dein AVENLOQ-Konto und alle zugehörigen Daten wurden "
            "unwiderruflich gelöscht."
        ),
    )
