"""Zentraler Datei-Storage-Layer - der EINE Ort, an dem Dokumentbytes

gelesen/geschrieben/gelöscht werden (siehe CLAUDE.md "Clevoriq Library
Service"). `speicher.py` ist der einzige Aufrufer; kein UI-Modul greift
direkt auf lokale Dateipfade zu.

Zwei Backends mit identischer Schnittstelle:

- `LocalFileStorage` — heutiges Entwicklungs-Backend, Dateien unter
  `app_daten/` (siehe `speicher.APP_DATEN_ORDNER`).
- `S3Storage` — für den späteren produktiven Wechsel auf S3-kompatiblen
  IONOS Object Storage vorbereitet (Konfiguration ausschließlich über
  `CLEVORIQ_S3_*`-Umgebungsvariablen, siehe `.env.example`). In diesem
  Architekturblock bewusst NICHT gegen eine echte Cloud getestet - nur
  gegen einen im Test hand-geschriebenen Fake-Client (siehe
  `test_storage_db_backend.py`), damit die Logik ohne echte
  IONOS-/AWS-Verbindung überprüfbar ist.

Storage-Keys sind serverseitig erzeugte, opake Pfade der Form
`users/<user_id>/documents/<public_id>/original.<endung>` (siehe
`speicher.dokument_speichern`) - NIE aus einer Benutzereingabe (z. B.
dem Original-Dateinamen) abgeleitet. `storage_key_gueltig` validiert
JEDEN Key vor jeder Dateisystem-/S3-Operation gegen ein striktes
Zeichen-Whitelist-Format und lehnt `..`-Segmente, absolute Pfade und
Backslashes ab - die einzige Verteidigungslinie gegen Path Traversal
UND (bei `LocalFileStorage`) eine zusätzliche, redundante Prüfung, dass
der aufgelöste Pfad tatsächlich innerhalb des Basisordners liegt.

WICHTIG - ein Storage-Key ist NIEMALS eine Zugriffsberechtigung: jede
Funktion hier prüft nur Format/Existenz, nicht Eigentümerschaft. Die
Eigentümerschaftsprüfung (gehört dieses Dokument dem anfragenden
Benutzer?) bleibt ausschließlich Aufgabe von `speicher.py`, bevor ein
Storage-Key überhaupt an dieses Modul weitergereicht wird (siehe
CLAUDE.md "User-Isolation bleibt zwingend").
"""

import os
import re
import shutil
from pathlib import Path


BACKEND_LOCAL = "local"
BACKEND_S3 = "s3"

# Nur unverfängliche Pfad-Zeichen - kein Backslash, kein führendes "/"
# (das wäre ein absoluter Pfad), keine Sonderzeichen, die auf manchen
# Dateisystemen/S3-Implementierungen unterschiedlich interpretiert
# werden könnten.
_STORAGE_KEY_MUSTER = re.compile(r"^[A-Za-z0-9_\-./]+$")


class StorageFehler(Exception):
    """Einheitliche Ausnahme für JEDEN Storage-Fehler, egal ob lokal
    (OSError) oder S3 (botocore/boto3-Ausnahmen) - Aufrufer in
    `speicher.py` müssen nicht zwischen den beiden Backends
    unterscheiden. Die Nachricht enthält absichtlich NIE Secrets
    (Zugangsschlüssel, vollständige Endpunkt-URLs mit Credentials) oder
    Dokumentinhalte - siehe CLAUDE.md "Logging"."""


def storage_key_gueltig(key):
    """Prüft einen Storage-Key auf ein sicheres, erwartetes Format.

    Lehnt ab: leere/None-Werte, einen führenden "/" oder "\\" (absoluter
    Pfad bzw. Windows-Laufwerkspfad), jedes ".."-Segment (Path
    Traversal) und jedes Zeichen außerhalb des Whitelist-Musters.
    """
    if not key or key.startswith("/") or key.startswith("\\") or "\\" in key:
        return False

    if ".." in key.split("/"):
        return False

    return bool(_STORAGE_KEY_MUSTER.match(key))


def _praefix_gueltig(praefix):
    """Wie `storage_key_gueltig`, aber toleriert einen abschließenden
    "/" - für `praefix_loeschen` (z. B. "users/3/"), das selbst keinen
    einzelnen Objekt-Key, sondern einen Ordner-/Key-Präfix beschreibt."""
    bereinigt = praefix.rstrip("/")
    return bool(bereinigt) and storage_key_gueltig(bereinigt)


class LocalFileStorage:
    """Entwicklungs-Backend: Dateien unter einem lokalen Basisordner
    (`speicher.APP_DATEN_ORDNER`), Storage-Key = Pfad relativ dazu."""

    def __init__(self, basis_ordner):
        self._basis = Path(basis_ordner).resolve()
        self._basis.mkdir(parents=True, exist_ok=True)

    def _pfad(self, key):
        if not storage_key_gueltig(key):
            raise StorageFehler("Ungültiger Storage-Key.")

        pfad = (self._basis / key).resolve()

        # Redundante zweite Verteidigungslinie (siehe Moduldocstring):
        # selbst falls `storage_key_gueltig` künftig gelockert würde,
        # darf der aufgelöste Pfad NIE außerhalb des Basisordners landen.
        if pfad != self._basis and self._basis not in pfad.parents:
            raise StorageFehler("Storage-Key liegt außerhalb des Basisordners.")

        return pfad

    def speichern(self, key, daten):
        pfad = self._pfad(key)
        try:
            pfad.parent.mkdir(parents=True, exist_ok=True)
            pfad.write_bytes(daten)
        except OSError as fehler:
            raise StorageFehler("Datei konnte nicht gespeichert werden.") from fehler

    def lesen(self, key):
        pfad = self._pfad(key)

        if not pfad.exists():
            return None

        try:
            return pfad.read_bytes()
        except OSError as fehler:
            raise StorageFehler("Datei konnte nicht gelesen werden.") from fehler

    def existiert(self, key):
        return self._pfad(key).exists()

    def loeschen(self, key):
        pfad = self._pfad(key)
        try:
            pfad.unlink(missing_ok=True)
        except OSError as fehler:
            raise StorageFehler("Datei konnte nicht gelöscht werden.") from fehler

    def groesse(self, key):
        pfad = self._pfad(key)

        if not pfad.exists():
            return None

        try:
            return pfad.stat().st_size
        except OSError as fehler:
            raise StorageFehler("Dateigröße konnte nicht ermittelt werden.") from fehler

    def praefix_loeschen(self, praefix):
        """Löscht ALLE Objekte unterhalb eines Key-Präfixes (z. B. beim
        endgültigen Löschen eines Kontos, siehe
        `speicher.konto_endgueltig_loeschen`)."""
        if not _praefix_gueltig(praefix):
            raise StorageFehler("Ungültiger Storage-Präfix.")

        ordner = (self._basis / praefix.rstrip("/")).resolve()

        if ordner != self._basis and self._basis not in ordner.parents:
            raise StorageFehler("Storage-Präfix liegt außerhalb des Basisordners.")

        if not ordner.exists():
            return

        try:
            shutil.rmtree(ordner)
        except OSError as fehler:
            raise StorageFehler("Objekte konnten nicht vollständig gelöscht werden.") from fehler


def _ist_nicht_gefunden_fehler(fehler):
    """Erkennt einen S3 "Objekt nicht gefunden"-Fehler unabhängig davon,
    ob der jeweilige S3-kompatible Anbieter eine spezifische
    `NoSuchKey`-Ausnahmeklasse wirft oder nur einen generischen
    `ClientError` mit passendem Fehlercode."""
    antwort = getattr(fehler, "response", None)

    if not isinstance(antwort, dict):
        return False

    code = str(antwort.get("Error", {}).get("Code", ""))
    return code in ("NoSuchKey", "404", "NotFound")


class S3Storage:
    """S3-kompatibles Produktions-Backend (für IONOS Object Storage
    vorbereitet - IONOS ist S3-kompatibel, siehe CLAUDE.md). Nutzt
    `boto3`, importiert es aber erst bei tatsächlicher Verwendung (nicht
    beim Modul-Import), damit ein reiner `local`-Betrieb `boto3` nicht
    installiert haben muss.

    Erzeugt NIE ein Objekt mit öffentlichem Lese-Zugriff (kein
    `ACL="public-read"` irgendwo in diesem Modul) - der Bucket bleibt
    vollständig privat, siehe CLAUDE.md "Private Objects". Ein Download
    läuft ausschließlich über die Clevoriq-App selbst (`lesen`) oder -
    vorbereitet, aber in diesem Block an keiner UI-Stelle verwendet -
    über eine kurzlebige, erst NACH einer erfolgreichen
    Berechtigungsprüfung durch den Aufrufer erzeugte Presigned URL
    (`presigned_download_url`).
    """

    def __init__(self, endpoint, region, bucket, access_key_id, secret_access_key):
        self._bucket = bucket
        self._client = self._client_erstellen(endpoint, region, access_key_id, secret_access_key)

    @staticmethod
    def _client_erstellen(endpoint, region, access_key_id, secret_access_key):
        try:
            import boto3
        except ImportError as fehler:
            raise StorageFehler(
                "Das S3-Storage-Backend benötigt das Paket 'boto3' "
                "(siehe requirements.txt)."
            ) from fehler

        return boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
        )

    def speichern(self, key, daten):
        if not storage_key_gueltig(key):
            raise StorageFehler("Ungültiger Storage-Key.")

        try:
            self._client.put_object(Bucket=self._bucket, Key=key, Body=daten)
        except Exception as fehler:
            raise StorageFehler("Objekt konnte nicht gespeichert werden.") from fehler

    def lesen(self, key):
        if not storage_key_gueltig(key):
            raise StorageFehler("Ungültiger Storage-Key.")

        try:
            antwort = self._client.get_object(Bucket=self._bucket, Key=key)
            return antwort["Body"].read()
        except Exception as fehler:
            if _ist_nicht_gefunden_fehler(fehler):
                return None
            raise StorageFehler("Objekt konnte nicht gelesen werden.") from fehler

    def existiert(self, key):
        if not storage_key_gueltig(key):
            raise StorageFehler("Ungültiger Storage-Key.")

        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except Exception as fehler:
            if _ist_nicht_gefunden_fehler(fehler):
                return False
            raise StorageFehler("Objekt-Status konnte nicht geprüft werden.") from fehler

    def loeschen(self, key):
        if not storage_key_gueltig(key):
            raise StorageFehler("Ungültiger Storage-Key.")

        try:
            # S3-Semantik: das Löschen eines nicht (mehr) existierenden
            # Keys ist idempotent erfolgreich - kein Fehler, kein
            # Unterschied zu `LocalFileStorage.loeschen`s `missing_ok=True`.
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except Exception as fehler:
            raise StorageFehler("Objekt konnte nicht gelöscht werden.") from fehler

    def groesse(self, key):
        if not storage_key_gueltig(key):
            raise StorageFehler("Ungültiger Storage-Key.")

        try:
            antwort = self._client.head_object(Bucket=self._bucket, Key=key)
            return antwort.get("ContentLength")
        except Exception as fehler:
            if _ist_nicht_gefunden_fehler(fehler):
                return None
            raise StorageFehler("Objektgröße konnte nicht ermittelt werden.") from fehler

    def praefix_loeschen(self, praefix):
        if not _praefix_gueltig(praefix):
            raise StorageFehler("Ungültiger Storage-Präfix.")

        try:
            paginator = self._client.get_paginator("list_objects_v2")
            for seite in paginator.paginate(Bucket=self._bucket, Prefix=praefix):
                objekte = [{"Key": eintrag["Key"]} for eintrag in seite.get("Contents", [])]

                if objekte:
                    self._client.delete_objects(Bucket=self._bucket, Delete={"Objects": objekte})
        except Exception as fehler:
            raise StorageFehler("Objekte konnten nicht vollständig gelöscht werden.") from fehler

    def presigned_download_url(self, key, ablauf_sekunden=60):
        """Erzeugt eine kurzlebige, signierte Download-URL für EIN Objekt.

        WICHTIG: Diese Methode prüft selbst KEINE Eigentümerschaft - sie
        darf ausschließlich aufgerufen werden, NACHDEM der Aufrufer
        bereits (über `speicher.py`) geprüft hat, dass der anfragende
        Benutzer dieses Dokument tatsächlich besitzt (siehe CLAUDE.md
        "Private Objects"). Die URL wird nirgends dauerhaft gespeichert
        und nie geloggt - sie ist reine, kurzlebige Vorbereitung für ein
        künftiges Direkt-Download-Feature und wird in diesem
        Architekturblock von keiner UI-Stelle aufgerufen.
        """
        if not storage_key_gueltig(key):
            raise StorageFehler("Ungültiger Storage-Key.")

        try:
            return self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=ablauf_sekunden,
            )
        except Exception as fehler:
            raise StorageFehler("Download-Link konnte nicht erzeugt werden.") from fehler


def _s3_aus_umgebung():
    pflichtvariablen = {
        "CLEVORIQ_S3_ENDPOINT": os.environ.get("CLEVORIQ_S3_ENDPOINT", "").strip(),
        "CLEVORIQ_S3_REGION": os.environ.get("CLEVORIQ_S3_REGION", "").strip(),
        "CLEVORIQ_S3_BUCKET": os.environ.get("CLEVORIQ_S3_BUCKET", "").strip(),
        "CLEVORIQ_S3_ACCESS_KEY_ID": os.environ.get("CLEVORIQ_S3_ACCESS_KEY_ID", "").strip(),
        "CLEVORIQ_S3_SECRET_ACCESS_KEY": os.environ.get("CLEVORIQ_S3_SECRET_ACCESS_KEY", "").strip(),
    }

    fehlend = [name for name, wert in pflichtvariablen.items() if not wert]

    if fehlend:
        # Nennt NUR die fehlenden Variablennamen, nie einen (evtl.
        # teilweise gesetzten) Wert - dieselbe Vorsicht wie
        # `email_versand.py`s Resend-Konfigurationsprüfung.
        raise StorageFehler(
            "CLEVORIQ_STORAGE_BACKEND=s3 ist gesetzt, aber folgende "
            f"Umgebungsvariable(n) fehlen: {', '.join(fehlend)}."
        )

    return S3Storage(
        endpoint=pflichtvariablen["CLEVORIQ_S3_ENDPOINT"],
        region=pflichtvariablen["CLEVORIQ_S3_REGION"],
        bucket=pflichtvariablen["CLEVORIQ_S3_BUCKET"],
        access_key_id=pflichtvariablen["CLEVORIQ_S3_ACCESS_KEY_ID"],
        secret_access_key=pflichtvariablen["CLEVORIQ_S3_SECRET_ACCESS_KEY"],
    )


def aktuelles_backend():
    """Liest `CLEVORIQ_STORAGE_BACKEND` (Standard: `local`)."""
    wert = os.environ.get("CLEVORIQ_STORAGE_BACKEND", BACKEND_LOCAL).strip().lower()
    return wert or BACKEND_LOCAL


def storage_backend(lokaler_basis_ordner):
    """Fabrikfunktion: liefert die passende Storage-Implementierung für
    das konfigurierte Backend.

    `lokaler_basis_ordner` wird NUR für Backend `local` verwendet - vom
    Aufrufer (`speicher.py`) übergeben, statt hier selbst einen Pfad
    festzulegen, damit Tests, die `speicher.APP_DATEN_ORDNER` auf ein
    temporäres Verzeichnis umbiegen, automatisch auch den Storage-Layer
    mit umleiten (siehe `speicher._storage`). Für `s3` kommt der
    Speicherort ausschließlich aus den `CLEVORIQ_S3_*`-Umgebungsvariablen.
    """
    backend = aktuelles_backend()

    if backend == BACKEND_S3:
        return _s3_aus_umgebung()

    if backend != BACKEND_LOCAL:
        raise StorageFehler(
            f"Unbekanntes CLEVORIQ_STORAGE_BACKEND={backend!r} - erlaubt sind "
            f"{BACKEND_LOCAL!r} oder {BACKEND_S3!r}."
        )

    return LocalFileStorage(lokaler_basis_ordner)
