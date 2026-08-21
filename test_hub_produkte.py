"""Automatisierte Tests für Block 3 (Clevoriq Account + Hub + zentrale Library).

Deckt die Speicherschicht (`speicher.py`) und das Produktzugriffsmodell
(`produkte.py`) ab: Produktzugriffs-Vergabe, die zentrale, EINE
Dokumentbibliothek (kein Hub-/Documents-Duplikat), stabile
`public_id`s, Umbenennen/Löschen mit strikter Eigentümerschaftsprüfung
(IDOR-Schutz) und Migrationssicherheit.

Bewusst wie `test_auth_security.py` ein reines `unittest`-Skript (siehe
CLAUDE.md "keine Test-Suite konfiguriert") auf einer frischen,
temporären SQLite-Datenbank je Test - macht KEINE echten OpenAI- oder
Resend-Aufrufe: Chunks/Embeddings werden hier direkt mit
handgeschriebenen Fake-Vektoren über `speicher.chunks_speichern`
gespeichert, nie über `retrieval.embeddings_batch_erstellen`.

Ausführen mit:

    python test_hub_produkte.py

oder:

    python -m unittest test_hub_produkte
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

os.environ["CLEVORIQ_EMAIL_PROVIDER"] = "dev"
os.environ.pop("AVENLOQ_EMAIL_PROVIDER", None)
os.environ.pop("RESEND_API_KEY", None)

import produkte  # noqa: E402
import speicher  # noqa: E402


class _TempDbTestCase(unittest.TestCase):
    """Isoliert jede Testmethode auf einer frischen, temporären DB (siehe
    `test_auth_security.py`s gleichnamige Basisklasse)."""

    def setUp(self):
        self._tmp_dir = Path(tempfile.mkdtemp(prefix="clevoriq_hub_test_"))
        speicher.APP_DATEN_ORDNER = self._tmp_dir
        speicher.BENUTZER_ORDNER = self._tmp_dir / "users"
        speicher.DB_PFAD = self._tmp_dir / "bibliothek.db"
        speicher._ALTER_PDF_ORDNER = self._tmp_dir / "pdfs"
        speicher.datenbank_initialisieren()

    def tearDown(self):
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _neuer_benutzer(self, benutzername, email):
        return speicher.benutzer_erstellen(benutzername, email, "Passwort123")

    def _dokument_anlegen(self, benutzer_id, dateiname="vertrag.pdf", inhalt=b"Inhalt"):
        hash_wert = speicher.hash_berechnen(inhalt + dateiname.encode("utf-8"))
        dokument_id = speicher.dokument_speichern(
            dateiname, hash_wert, inhalt, 1, "pdf", "seite", benutzer_id
        )
        # Fake-Embedding statt eines echten OpenAI-Aufrufs (siehe CLAUDE.md
        # "Keine echten OpenAI-Aufrufe in Tests") - `chunks_speichern` selbst
        # ruft nie die API auf, das übernimmt ausschließlich `retrieval.py`.
        speicher.chunks_speichern(
            dokument_id,
            [{"seitennummer": 1, "text": "Beispieltext", "einheit_typ": "seite", "einheit_anzeige": None}],
            [[0.1, 0.2, 0.3]],
        )
        return dokument_id


class ProduktzugriffTests(_TempDbTestCase):
    def test_a_bestehender_benutzer_erhaelt_documents_berechtigung(self):
        """A) Ein VOR Einführung des Produktmodells angelegter Benutzer
        (hier simuliert über eine rohe INSERT-Zeile ohne
        `benutzer_erstellen`) bekommt beim nächsten Start automatisch
        aktiven Documents-Zugriff (additive Migration, siehe
        `speicher._produktzugriffe_migrieren`)."""
        with speicher._verbindung() as conn:
            jetzt = speicher._jetzt()
            cursor = conn.execute(
                "INSERT INTO benutzer (benutzername, email, passwort_hash, erstellt_am, "
                "aktualisiert_am, aktiv, email_verified) VALUES (?, ?, ?, ?, ?, 1, 1)",
                ("altkonto", "alt@example.com", "x", jetzt, jetzt),
            )
            alt_benutzer_id = cursor.lastrowid

        self.assertFalse(speicher.produkt_zugriff_aktiv(alt_benutzer_id, produkte.PRODUKT_DOCUMENTS))

        speicher.datenbank_initialisieren()

        self.assertTrue(speicher.produkt_zugriff_aktiv(alt_benutzer_id, produkte.PRODUKT_DOCUMENTS))

    def test_b_neuer_benutzer_erhaelt_documents_berechtigung(self):
        """B) Ein über `benutzer_erstellen` (normale Registrierung) neu
        angelegtes Konto bekommt Documents-Zugriff sofort, ohne dass
        die Migration erneut laufen muss."""
        benutzer_id = self._neuer_benutzer("neu", "neu@example.com")
        self.assertTrue(speicher.produkt_zugriff_aktiv(benutzer_id, produkte.PRODUKT_DOCUMENTS))

    def test_e_gesperrter_produktzugriff_wird_blockiert(self):
        """E) Die zentrale, serverseitige Zugriffsprüfung (die
        `web_app.py`s Bereichs-Schranke zugrunde liegt) verweigert
        Zugriff, sobald der Status nicht mehr "aktiv" ist - unabhängig
        davon, ob irgendein UI-Button sichtbar/versteckt ist."""
        benutzer_id = self._neuer_benutzer("gesperrt", "gesperrt@example.com")
        self.assertTrue(speicher.produkt_zugriff_aktiv(benutzer_id, produkte.PRODUKT_DOCUMENTS))

        with speicher._verbindung() as conn:
            conn.execute(
                "UPDATE produkt_zugriffe SET status = 'gesperrt' WHERE user_id = ? AND product_key = ?",
                (benutzer_id, produkte.PRODUKT_DOCUMENTS),
            )

        self.assertFalse(speicher.produkt_zugriff_aktiv(benutzer_id, produkte.PRODUKT_DOCUMENTS))

    def test_e2_abgelaufener_produktzugriff_wird_blockiert(self):
        """E) Ein in der Vergangenheit liegendes `laeuft_ab_am` deaktiviert
        den Zugriff, selbst wenn `status` noch "aktiv" ist."""
        benutzer_id = self._neuer_benutzer("abgelaufen", "abgelaufen@example.com")

        with speicher._verbindung() as conn:
            conn.execute(
                "UPDATE produkt_zugriffe SET laeuft_ab_am = '2000-01-01T00:00:00' "
                "WHERE user_id = ? AND product_key = ?",
                (benutzer_id, produkte.PRODUKT_DOCUMENTS),
            )

        self.assertFalse(speicher.produkt_zugriff_aktiv(benutzer_id, produkte.PRODUKT_DOCUMENTS))

    def test_t_produktzugriff_von_a_beeinflusst_b_nicht(self):
        """T) Produktberechtigungen sind strikt je Benutzer - eine Sperre
        bei Benutzer A darf Benutzer B nicht mit betreffen."""
        benutzer_a = self._neuer_benutzer("a", "a@example.com")
        benutzer_b = self._neuer_benutzer("b", "b@example.com")

        with speicher._verbindung() as conn:
            conn.execute(
                "UPDATE produkt_zugriffe SET status = 'gesperrt' WHERE user_id = ? AND product_key = ?",
                (benutzer_a, produkte.PRODUKT_DOCUMENTS),
            )

        self.assertFalse(speicher.produkt_zugriff_aktiv(benutzer_a, produkte.PRODUKT_DOCUMENTS))
        self.assertTrue(speicher.produkt_zugriff_aktiv(benutzer_b, produkte.PRODUKT_DOCUMENTS))

    def test_zugriff_gewaehren_ueberschreibt_bestehende_zeile_nicht(self):
        """`produkt_zugriff_gewaehren` darf eine bereits bestehende
        Zugriffszeile (z. B. eine manuelle Sperre) NIE stillschweigend
        wieder auf "aktiv" zurücksetzen."""
        benutzer_id = self._neuer_benutzer("bestand", "bestand@example.com")

        with speicher._verbindung() as conn:
            conn.execute(
                "UPDATE produkt_zugriffe SET status = 'gesperrt' WHERE user_id = ? AND product_key = ?",
                (benutzer_id, produkte.PRODUKT_DOCUMENTS),
            )

        speicher.produkt_zugriff_gewaehren(benutzer_id, produkte.PRODUKT_DOCUMENTS)

        self.assertFalse(speicher.produkt_zugriff_aktiv(benutzer_id, produkte.PRODUKT_DOCUMENTS))


class ZentraleLibraryTests(_TempDbTestCase):
    def test_f_zentrale_library_zeigt_bestehende_dokumente(self):
        """F) Die zentrale Bibliotheksabfrage (von Hub UND Documents
        gleichermaßen genutzt, siehe `bibliothek_ansicht.rendern`)
        zeigt ein gespeichertes Dokument."""
        benutzer_id = self._neuer_benutzer("lib", "lib@example.com")
        self._dokument_anlegen(benutzer_id, "rechnung.pdf")

        dokumente = speicher.dokumente_laden(benutzer_id)

        self.assertEqual(len(dokumente), 1)
        self.assertEqual(dokumente[0]["dateiname"], "rechnung.pdf")

    def test_g_h_hub_und_documents_teilen_dieselbe_quelle(self):
        """G+H) Es gibt nur EINE zentrale Bibliotheksabfrage - ein "Upload"
        (hier: `dokument_speichern`, dieselbe Funktion, die sowohl vom
        Hub- als auch vom Documents-Bibliotheksaufruf in `web_app.py`
        verwendet wird) ist über `dokumente_laden` sofort sichtbar,
        unabhängig davon, "von wo" man fragt - es gibt keine zweite,
        separate Datenquelle, die erst synchronisiert werden müsste."""
        benutzer_id = self._neuer_benutzer("sync", "sync@example.com")
        self._dokument_anlegen(benutzer_id, "hub_upload.pdf")

        # "Hub-Ansicht" und "Documents-Ansicht" sind in dieser Architektur
        # exakt derselbe Aufruf - hier zweimal hintereinander ausgeführt,
        # um zu zeigen, dass es keinen unterschiedlichen Zustand gibt.
        hub_sicht = speicher.dokumente_laden(benutzer_id)
        documents_sicht = speicher.dokumente_laden(benutzer_id)

        self.assertEqual(hub_sicht, documents_sicht)
        self.assertEqual(len(hub_sicht), 1)

    def test_i_j_umbenennen_ist_ueberall_sofort_sichtbar(self):
        """I+J) Umbenennen (egal "von wo" aufgerufen) ändert die EINE
        gespeicherte Zeile - jede folgende Abfrage (Hub oder Documents)
        sieht den neuen Namen."""
        benutzer_id = self._neuer_benutzer("rename", "rename@example.com")
        dokument_id = self._dokument_anlegen(benutzer_id, "alt.pdf")

        self.assertTrue(speicher.dokument_umbenennen(dokument_id, benutzer_id, "neu.pdf"))

        self.assertEqual(speicher.dokumente_laden(benutzer_id)[0]["dateiname"], "neu.pdf")

    def test_k_l_loeschen_entfernt_dokument_ueberall(self):
        """K+L) Löschen (egal "von wo" aufgerufen) entfernt die EINE
        gespeicherte Zeile - das Dokument verschwindet aus jeder
        folgenden Abfrage."""
        benutzer_id = self._neuer_benutzer("del", "del@example.com")
        dokument_id = self._dokument_anlegen(benutzer_id, "loeschen.pdf")

        speicher.dokument_loeschen(dokument_id, benutzer_id)

        self.assertEqual(speicher.dokumente_laden(benutzer_id), [])

    def test_m_keine_dateiduplikation(self):
        """M) Es gibt keinen zweiten Dateispeicher für Hub vs. Documents -
        genau eine physische Datei pro Dokument."""
        benutzer_id = self._neuer_benutzer("dup", "dup@example.com")
        self._dokument_anlegen(benutzer_id, "einzel.pdf")

        ordner = speicher._benutzer_dokumente_ordner(benutzer_id)
        dateien = list(ordner.iterdir())

        self.assertEqual(len(dateien), 1)


class ZentraleDokumentIdTests(_TempDbTestCase):
    def test_n_bestehende_dokumente_erhalten_stabile_public_id(self):
        """N) Ein Dokument, das (simuliert) VOR Einführung der `public_id`
        gespeichert wurde, bekommt beim nächsten Start idempotent eine
        stabile UUID (siehe `speicher._dokumente_public_ids_ergaenzen`)."""
        benutzer_id = self._neuer_benutzer("altdoc", "altdoc@example.com")
        dokument_id = self._dokument_anlegen(benutzer_id, "alt.pdf")

        with speicher._verbindung() as conn:
            conn.execute("UPDATE dokumente SET public_id = NULL WHERE id = ?", (dokument_id,))

        self.assertIsNone(speicher.dokumente_laden(benutzer_id)[0]["public_id"])

        speicher.datenbank_initialisieren()

        oeffentliche_id = speicher.dokumente_laden(benutzer_id)[0]["public_id"]
        self.assertIsNotNone(oeffentliche_id)
        self.assertTrue(len(oeffentliche_id) >= 32)

    def test_o_public_id_bleibt_ueber_neustarts_stabil(self):
        """O) Ein bereits vorhandener Wert wird durch einen erneuten
        "Neustart" (erneuter `datenbank_initialisieren`-Aufruf) NICHT
        verändert."""
        benutzer_id = self._neuer_benutzer("stabil", "stabil@example.com")
        self._dokument_anlegen(benutzer_id, "stabil.pdf")

        vorher = speicher.dokumente_laden(benutzer_id)[0]["public_id"]
        speicher.datenbank_initialisieren()
        nachher = speicher.dokumente_laden(benutzer_id)[0]["public_id"]

        self.assertEqual(vorher, nachher)

    def test_neue_dokumente_erhalten_sofort_eine_public_id(self):
        benutzer_id = self._neuer_benutzer("frisch", "frisch@example.com")
        self._dokument_anlegen(benutzer_id, "frisch.pdf")

        self.assertIsNotNone(speicher.dokumente_laden(benutzer_id)[0]["public_id"])


class IsolationTests(_TempDbTestCase):
    """P-S: User A darf auf Dokumente von User B unter KEINEN Umständen
    zugreifen - weder über die interne ID noch über die `public_id`,
    weder lesend noch schreibend (umbenennen/löschen)."""

    def setUp(self):
        super().setUp()
        self.benutzer_a = self._neuer_benutzer("isoliert_a", "isoliert_a@example.com")
        self.benutzer_b = self._neuer_benutzer("isoliert_b", "isoliert_b@example.com")
        self.dokument_b_id = self._dokument_anlegen(self.benutzer_b, "geheim_b.pdf")
        self.dokument_b_public_id = speicher.dokumente_laden(self.benutzer_b)[0]["public_id"]

    def test_p_kein_zugriff_ueber_interne_id_oder_public_id(self):
        """P) Weder die interne ID noch die `public_id` von B's Dokument
        liefern für A irgendetwas zurück."""
        self.assertIsNone(speicher.dokument_datei_lesen(self.dokument_b_id, self.benutzer_a))
        self.assertIsNone(
            speicher.dokument_nach_public_id(self.dokument_b_public_id, self.benutzer_a)
        )
        # A's eigene Bibliothek bleibt leer - B's Dokument taucht dort nicht auf.
        self.assertEqual(speicher.dokumente_laden(self.benutzer_a), [])

    def test_q_kein_umbenennen_fremder_dokumente(self):
        """Q) A kann B's Dokument nicht umbenennen."""
        erfolg = speicher.dokument_umbenennen(self.dokument_b_id, self.benutzer_a, "gekapert.pdf")

        self.assertFalse(erfolg)
        self.assertEqual(speicher.dokumente_laden(self.benutzer_b)[0]["dateiname"], "geheim_b.pdf")

    def test_r_kein_loeschen_fremder_dokumente(self):
        """R) A kann B's Dokument nicht löschen."""
        speicher.dokument_loeschen(self.dokument_b_id, self.benutzer_a)

        self.assertEqual(len(speicher.dokumente_laden(self.benutzer_b)), 1)

    def test_s_keine_fremden_chunks_ueber_manipulierte_id(self):
        """S) A kann B's Dokumentinhalte nicht über eine (z. B. erratene
        oder aus einer eigenen Auswahl manipulierte) `dokument_id` von B
        laden lassen - Grundlage jeder Documents-Analyse/-Prüfung. Der
        Login-JOIN in `chunks_laden` filtert IMMER zusätzlich nach
        `d.user_id = ?`."""
        chunks = speicher.chunks_laden([self.dokument_b_id], self.benutzer_a)

        self.assertEqual(chunks, [])


class MigrationUndBestandsdatenTests(_TempDbTestCase):
    def test_u_bestehende_chats_funktionieren_nach_migration_weiter(self):
        """U) Ein Chat mit einer Dokumentzuordnung bleibt nach einer
        erneuten Migration (`datenbank_initialisieren`) vollständig
        nutzbar - Dokument-Zuordnung und Inhalt bleiben erhalten."""
        benutzer_id = self._neuer_benutzer("chatuser", "chatuser@example.com")
        dokument_id = self._dokument_anlegen(benutzer_id, "chat_doc.pdf")

        chat_id = speicher.chat_erstellen(benutzer_id)
        speicher.chat_dokumente_setzen(chat_id, [dokument_id], benutzer_id)
        speicher.nachricht_hinzufuegen(chat_id, benutzer_id, "Frage?", "Antwort.", [])

        speicher.datenbank_initialisieren()

        chat = speicher.chat_laden(chat_id, benutzer_id)
        self.assertEqual(chat["dokument_ids"], [dokument_id])
        self.assertEqual(len(chat["nachrichten"]), 1)
        self.assertEqual(chat["nachrichten"][0]["frage"], "Frage?")

    def test_w_migration_zweimal_hintereinander_fehlerfrei(self):
        """W) `datenbank_initialisieren` beliebig oft ausführbar, ohne
        Fehler und ohne Datenverlust/-duplikation."""
        benutzer_id = self._neuer_benutzer("doppelt", "doppelt@example.com")
        self._dokument_anlegen(benutzer_id, "doppelt.pdf")

        speicher.datenbank_initialisieren()
        speicher.datenbank_initialisieren()

        self.assertEqual(len(speicher.dokumente_laden(benutzer_id)), 1)
        self.assertEqual(
            len(speicher.produkte_des_benutzers(benutzer_id)), 1
        )


if __name__ == "__main__":
    unittest.main()
