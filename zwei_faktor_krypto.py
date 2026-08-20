"""TOTP-/Verschlüsselungs-/Backup-Code-Logik für die Zwei-Faktor-Authentifizierung.

Bewusst frei von Streamlit- und Datenbank-Importen - reine, leicht
isoliert testbare Logik, analog zu `auth.py`s Rolle für Passwörter. Die
Datenbank-seitige Persistenz (verschlüsseltes Secret, Backup-Code-Hashes,
Challenges) lebt in `speicher.py`, die Streamlit-UI in `benutzer.py`/
`konto.py` - dieses Modul kennt keines von beiden.

Nutzt ausschließlich etablierte, gepflegte Bibliotheken statt eigener
Kryptographie: `pyotp` für RFC-6238-konformes TOTP (kompatibel mit jeder
gängigen Authenticator-App - Google/Microsoft Authenticator, Authy,
1Password, Bitwarden, ...; keine proprietäre Anbieter-Abhängigkeit) und
`cryptography.fernet.Fernet` für authentifizierte Verschlüsselung des
TOTP-Secrets (AES-128-CBC + HMAC-SHA256, versioniert, mit Ablauf-Präfix -
ein etablierter, auditierter Standardbaustein statt eines selbstgebauten
Verschlüsselungsschemas).

WICHTIG: Ein TOTP-Secret kann nicht gehasht werden (es wird zur
Code-Prüfung im Klartext gebraucht) - es wird deshalb ausschließlich
VERSCHLÜSSELT gespeichert (siehe `secret_verschluesseln`/
`secret_entschluesseln`), nie im Klartext. Der Verschlüsselungsschlüssel
kommt ausschließlich aus einer Umgebungsvariable (`CLEVORIQ_2FA_ENCRYPTION_KEY`)
- niemals im Code hinterlegt, niemals geloggt, niemals automatisch in
Produktion erzeugt. Fehlt der Schlüssel oder ist er ungültig, wird ein
`RuntimeError` geworfen (sicheres Fehlschlagen) statt eines unsicheren
Fallbacks (z. B. Klartext-Speicherung oder ein fest einprogrammierter
Ersatzschlüssel).
"""

import os
import re
import secrets
import time

import pyotp
from cryptography.fernet import Fernet, InvalidToken


ISSUER = "Clevoriq"

TOTP_SCHRITT_SEKUNDEN = 30
# ±1 Zeitschritt (siehe Aufgabenstellung "konservative Zeitabweichung") -
# ergibt ein effektives Akzeptanzfenster von 90 Sekunden und toleriert
# damit kleine, normale Uhrabweichungen zwischen Server und Smartphone,
# ohne das Fenster für Brute-Force-Versuche unnötig zu vergrößern.
TOTP_FENSTER_SCHRITTE = 1

# Aktuelle Verschlüsselungs-Schlüsselversion. Das DB-Schema speichert die
# Version je verschlüsseltem Secret mit (siehe `speicher.py`s
# `secret_key_version`/`pending_key_version`) - eine künftige
# Schlüsselrotation müsste nur eine neue Versionsnummer + eine neue
# Umgebungsvariable (`CLEVORIQ_2FA_ENCRYPTION_KEY_V2`, siehe
# `_umgebungsvariable_fuer_version`) einführen und weiterhin ältere
# Versionen zum Entschlüsseln bestehender Secrets lesen können, ohne
# dass sich an diesem Modul sonst etwas ändern müsste. Für diesen
# Arbeitsblock wird nur Version 1 tatsächlich genutzt - eine Rotation
# selbst (Neuverschlüsselung aller bestehenden Secrets) ist bewusst
# NICHT Teil dieses Blocks.
AKTUELLE_SCHLUESSEL_VERSION = 1

_ENV_VAR = "CLEVORIQ_2FA_ENCRYPTION_KEY"

_BACKUP_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # ohne 0/O/1/I/L (Verwechslungsgefahr)
_BACKUP_CODE_LAENGE = 10


def _umgebungsvariable_fuer_version(version):
    if version == AKTUELLE_SCHLUESSEL_VERSION:
        return _ENV_VAR
    return f"{_ENV_VAR}_V{version}"


def _schluessel_fuer_version(version):
    """Lädt den Fernet-Schlüssel für eine bestimmte Version AUSSCHLIESSLICH
    aus der Umgebung. Wirft `RuntimeError` (mit einer Meldung, die NIEMALS
    den Schlüsselwert selbst enthält) bei fehlendem oder ungültigem
    Schlüssel - das ist das geforderte "sichere Fehlschlagen", nie ein
    stiller Klartext-Fallback.
    """
    env_name = _umgebungsvariable_fuer_version(version)
    roh = os.environ.get(env_name, "").strip()

    if not roh:
        raise RuntimeError(
            f"Umgebungsvariable {env_name} fehlt - 2FA-Verschlüsselung ist "
            "nicht verfügbar. Zwei-Faktor-Authentifizierung kann ohne "
            "gültigen Verschlüsselungsschlüssel nicht sicher betrieben werden."
        )

    try:
        return Fernet(roh.encode("utf-8"))
    except Exception:
        raise RuntimeError(
            f"Umgebungsvariable {env_name} enthält keinen gültigen "
            "Fernet-Schlüssel (44 Zeichen, URL-sicheres Base64)."
        ) from None


def schluessel_konfiguriert(version=AKTUELLE_SCHLUESSEL_VERSION):
    """Prüft, ob ein gültiger Schlüssel für diese Version vorhanden ist,
    OHNE bei einem Problem eine Ausnahme zu werfen - für UI-Stellen, die
    2FA-Einrichtung vorab deaktivieren wollen, statt den Benutzer erst
    mitten im Setup auf einen Fehler laufen zu lassen."""
    try:
        _schluessel_fuer_version(version)
        return True
    except RuntimeError:
        return False


def secret_verschluesseln(klartext_secret):
    """Verschlüsselt ein TOTP-Secret mit dem AKTUELLEN Schlüssel. Gibt
    `(chiffrat, schluessel_version)` zurück - beides wird in der DB
    gespeichert (nie das Secret selbst)."""
    schluessel = _schluessel_fuer_version(AKTUELLE_SCHLUESSEL_VERSION)
    chiffrat = schluessel.encrypt(klartext_secret.encode("utf-8")).decode("ascii")
    return chiffrat, AKTUELLE_SCHLUESSEL_VERSION


def secret_entschluesseln(chiffrat, schluessel_version):
    """Entschlüsselt ein gespeichertes TOTP-Secret. Wirft `RuntimeError`
    bei fehlendem/ungültigem Schlüssel ODER beschädigten/manipulierten
    Daten (`InvalidToken`) - Aufrufer MÜSSEN das als "2FA-Prüfung aktuell
    nicht möglich" behandeln, NIEMALS als "Code falsch" (das wäre ein
    irreführendes Signal) und NIEMALS die Prüfung überspringen."""
    schluessel = _schluessel_fuer_version(schluessel_version)

    try:
        klartext = schluessel.decrypt(chiffrat.encode("ascii"))
    except InvalidToken:
        raise RuntimeError(
            "2FA-Secret konnte nicht entschlüsselt werden (ungültiger "
            "Schlüssel oder beschädigte Daten)."
        ) from None

    return klartext.decode("utf-8")


def neues_totp_secret():
    """Erzeugt ein neues, kryptographisch zufälliges Base32-TOTP-Secret
    (`pyotp.random_base32`, Standardlänge 32 Zeichen = 160 Bit Entropie)."""
    return pyotp.random_base32()


def otpauth_uri(secret, email):
    """Baut die standardkonforme `otpauth://`-URI für den QR-Code -
    Issuer "Clevoriq", Account die E-Mail-Adresse des Nutzers. Jede
    RFC-6238-kompatible Authenticator-App (Google/Microsoft Authenticator,
    Authy, 1Password, Bitwarden, ...) kann diese URI scannen; es gibt
    keine anbieterspezifische Abhängigkeit."""
    return pyotp.TOTP(secret, interval=TOTP_SCHRITT_SEKUNDEN).provisioning_uri(
        name=email, issuer_name=ISSUER
    )


def _code_normalisieren(code):
    return re.sub(r"\s+", "", code or "")


def totp_code_pruefen(secret, code, letzter_zeitschritt=None, jetzt=None):
    """Prüft einen 6-stelligen TOTP-Code gegen `secret` mit einem
    Toleranzfenster von ±`TOTP_FENSTER_SCHRITTE` Zeitschritten. Gibt
    `(gueltig: bool, verwendeter_zeitschritt: int|None)` zurück.

    Replay-Schutz: Ist `letzter_zeitschritt` gesetzt (der zuletzt
    erfolgreich verwendete Zeitschritt dieses Benutzers, siehe
    `speicher.zwei_faktor_totp_pruefen`), wird ein Zeitschritt <= diesem
    Wert NICHT erneut akzeptiert - verhindert, dass ein einmal
    beobachteter/abgefangener Code innerhalb desselben Zeitfensters ein
    zweites Mal funktioniert, ohne normale kleine Uhrabweichungen zu
    bestrafen (jeder NEUE, spätere Zeitschritt bleibt weiterhin gültig).

    Vergleicht Codes ausschließlich über `pyotp.utils.strings_equal`
    (zeitkonstanter Vergleich aus der etablierten Bibliothek) statt `==`.
    """
    code = _code_normalisieren(code)

    if not code or len(code) != 6 or not code.isdigit():
        return False, None

    totp = pyotp.TOTP(secret, interval=TOTP_SCHRITT_SEKUNDEN)
    bezugszeit = jetzt if jetzt is not None else time.time()
    aktueller_schritt = int(bezugszeit // TOTP_SCHRITT_SEKUNDEN)

    for delta in range(-TOTP_FENSTER_SCHRITTE, TOTP_FENSTER_SCHRITTE + 1):
        schritt = aktueller_schritt + delta
        erwarteter_code = totp.at(schritt * TOTP_SCHRITT_SEKUNDEN)

        if pyotp.utils.strings_equal(erwarteter_code, code):
            if letzter_zeitschritt is not None and schritt <= letzter_zeitschritt:
                return False, None
            return True, schritt

    return False, None


def backup_codes_erzeugen(anzahl):
    """Erzeugt `anzahl` kryptographisch zufällige Backup-Codes im
    Klartext (Format "XXXXX-XXXXX", Alphabet ohne verwechselbare Zeichen,
    10 Nutzzeichen = 50 Bit Entropie je Code) - NUR zur einmaligen Anzeige
    unmittelbar nach Erstellung gedacht; der Aufrufer speichert
    ausschließlich Hashes (siehe `speicher.py`)."""
    return [_backup_code_erzeugen() for _ in range(anzahl)]


def _backup_code_erzeugen():
    roh = "".join(secrets.choice(_BACKUP_CODE_ALPHABET) for _ in range(_BACKUP_CODE_LAENGE))
    return f"{roh[:5]}-{roh[5:]}"


def backup_code_normalisieren(code):
    """Entfernt Formatierung (Leerzeichen/Bindestriche) und normalisiert
    Groß-/Kleinschreibung - für konsistentes Hashing/Vergleichen
    unabhängig davon, wie der Nutzer den Code eintippt (mit oder ohne
    Bindestrich, groß oder klein)."""
    return re.sub(r"[\s-]", "", (code or "").upper())
