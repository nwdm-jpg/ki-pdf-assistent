"""Passwort-Hashing und Eingabe-Validierung für die Benutzer-Authentifizierung.

Bewusst frei von Streamlit- und Datenbank-Importen: reine, leicht isoliert
testbare Logik (Hashing/Verifikation, Validierungsregeln). Die
Datenbank-seitige Persistenz von Benutzerkonten lebt in `speicher.py`,
die Streamlit-Anmelde-/Registrierungs-UI in `benutzer.py` - dieses Modul
kennt keine der beiden.

Passwort-Hashing nutzt Argon2 (Paket `argon2-cffi`) statt eines
selbstgeschriebenen Verfahrens: Argon2id ist der aktuelle empfohlene
Algorithmus für Passwort-Hashes (Gewinner der Password Hashing
Competition), speicher- und CPU-hart gegen Brute-Force/GPU-Angriffe, und
die Bibliothek übernimmt Salt-Erzeugung sowie das sichere
Parameter-Encoding im Hash-String selbst - es wird nirgends ein
Klartext-Passwort oder ein eigenes Salt gespeichert oder geloggt.
"""

import re

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError, InvalidHash


_ph = PasswordHasher()

MINDEST_PASSWORT_LAENGE = 8

_EMAIL_MUSTER = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Bewusst nur ein einfaches, permissives Zeichen-Set (Buchstaben, Ziffern,
# Unterstrich, Punkt, Bindestrich) statt eines exotischen Regelwerks -
# verhindert vor allem Leerzeichen und Sonderzeichen, die später als
# Teil von Dateipfaden oder URLs Probleme machen könnten, ohne legitime
# Benutzernamen unnötig einzuschränken.
_BENUTZERNAME_MUSTER = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")


def passwort_hash(passwort):
    """Erzeugt einen Argon2-Hash für ein Klartext-Passwort.

    Der zurückgegebene String enthält Algorithmus, Parameter und Salt
    zusammen mit dem eigentlichen Hash (Standard-Encoding von Argon2) -
    er kann direkt und vollständig in der Datenbank gespeichert werden.
    """
    return _ph.hash(passwort)


def passwort_pruefen(passwort, gespeicherter_hash):
    """Prüft ein Klartext-Passwort gegen einen gespeicherten Argon2-Hash.

    Gibt bei jedem Fehler (falsches Passwort, beschädigter/fremder
    Hash) einheitlich `False` zurück, statt die Ausnahme durchzureichen
    - Aufrufer müssen nicht zwischen "falsches Passwort" und "technischer
    Fehler beim Prüfen" unterscheiden, was versehentliches Leaken von
    Interna über unterschiedliches Fehlerverhalten vermeidet.
    """
    try:
        return _ph.verify(gespeicherter_hash, passwort)
    except (VerifyMismatchError, VerificationError, InvalidHash):
        return False


def email_gueltig(email):
    """Einfache, formale E-Mail-Prüfung (kein Versand/DNS-Check)."""
    return bool(_EMAIL_MUSTER.match((email or "").strip()))


def benutzername_gueltig(benutzername):
    """Prüft Grundform eines Benutzernamens (3-32 Zeichen, kein Leerraum)."""
    return bool(_BENUTZERNAME_MUSTER.match((benutzername or "").strip()))


def passwort_stark_genug(passwort):
    """Prüft nur die Mindestlänge - bewusst kein übertriebenes Regelwerk
    (Sonderzeichen-/Großbuchstaben-Pflicht u. Ä.) für dieses lokale MVP."""
    return len(passwort or "") >= MINDEST_PASSWORT_LAENGE
