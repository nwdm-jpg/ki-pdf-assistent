"""Minimales Security-/Audit-Logging für sicherheitsrelevante Ereignisse.

Schreibt in dieselbe `security_events`-Tabelle, die auch die
Datengrundlage für das persistente Rate-Limiting liefert (siehe
`speicher.py`/`ratenbegrenzung.py`). Enthält bewusst NIE Passwörter,
rohe Tokens oder API-Keys, und möglichst wenig personenbezogene Daten:
nur die eingegebene Login-Kennung bzw. E-Mail-Adresse bei Auth-Ereignissen
(nicht Namen, keine Dokumentinhalte, keine Datei-/Chat-Inhalte).

Aktuell rein lokal (SQLite) und ohne aktives Monitoring/Alerting -
`speicher.sicherheitsereignis_speichern` ist die einzige Schreibstelle,
sodass ein künftiges Monitoring (z. B. Export/Streaming an ein externes
System) hier an einer einzigen Stelle ansetzen kann, ohne jede
Aufrufstelle im Code einzeln anzufassen.
"""

import speicher


EREIGNIS_LOGIN_ERFOLG = "login_success"
EREIGNIS_LOGIN_FEHLGESCHLAGEN = "login_failed"
EREIGNIS_REGISTRIERUNG = "register"
EREIGNIS_RATE_LIMIT = "rate_limit_triggered"
EREIGNIS_PASSWORT_GEAENDERT = "password_changed"
EREIGNIS_PASSWORT_RESET_ANGEFORDERT = "password_reset_requested"
EREIGNIS_PASSWORT_RESET_ABGESCHLOSSEN = "password_reset_completed"
EREIGNIS_EMAIL_GEAENDERT = "email_changed"
EREIGNIS_EMAIL_BESTAETIGT = "email_verified"
EREIGNIS_VERIFIZIERUNG_ANGEFORDERT = "verification_resent"
EREIGNIS_VERIFIZIERUNG_FEHLGESCHLAGEN = "email_verification_failed"
EREIGNIS_LOGOUT = "logout"
EREIGNIS_SITZUNG_ABGELAUFEN = "session_invalidated"
EREIGNIS_KONTO_GELOESCHT = "account_deleted"


def protokollieren(event_type, user_id=None, identitaet=None, ip=None, erfolgreich=True, detail=None):
    """Schreibt ein einzelnes Audit-Ereignis. `identitaet` wird - sofern
    gesetzt - klein geschrieben/getrimmt gespeichert, konsistent mit der
    Normalisierung in `ratenbegrenzung.py`."""
    identitaet = (identitaet or "").strip().lower() or None
    speicher.sicherheitsereignis_speichern(event_type, user_id, identitaet, ip, erfolgreich, detail)
