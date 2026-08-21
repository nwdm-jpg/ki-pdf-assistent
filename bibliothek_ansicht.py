"""Die EINE zentrale Bibliotheksoberfläche - die "Clevoriq Library".

Wird identisch von zwei Einstiegspunkten in `web_app.py` aufgerufen:
Clevoriq Hub -> "Zentrale Bibliothek" UND Clevoriq Documents ->
"Dokumentenbibliothek". Es gibt bewusst KEINE zweite Implementierung
und KEINE Kopie - beide Aufrufstellen rendern exakt dieselbe Funktion
gegen exakt dieselben `speicher.py`-Funktionen (`dokumente_laden`,
`dokument_speichern`, `dokument_umbenennen`, `dokument_loeschen`, alle
bereits eigentümerschaftsgeprüft), sodass eine Änderung (Upload,
Umbenennen, Löschen) auf der einen Seite ohne jeden Synchronisations-
schritt sofort auf der anderen sichtbar ist: Streamlit führt bei jeder
Interaktion das komplette Skript neu aus und beide Aufrufstellen lesen
dabei live aus derselben SQLite-Datenbank - es gibt keinen Cache und
keine zweite Kopie der Daten.

Enthält alle bestehenden Bibliotheksfunktionen (Upload, Suche, Filter,
Sortierung, Löschen) unverändert plus neu: Umbenennen (siehe
`speicher.dokument_umbenennen`) - vorher gab es dafür keine Funktion.
"""

from datetime import datetime, timedelta

import streamlit as st

import dokument_verarbeitung
import dokumentbibliothek
import komponenten
import speicher


def rendern(benutzer_id, dateien_verarbeiten):
    """Rendert die vollständige Bibliotheksansicht für `benutzer_id`.

    `dateien_verarbeiten(dateien, benutzer_id)` ist die einzige
    Verarbeitungs-/Speicherimplementierung der App (siehe `web_app.py`)
    und wird hier nur aufgerufen, nie dupliziert.
    """
    komponenten.seiten_hero(
        "📚",
        "Clevoriq Library",
        "Alle deine Dokumente an einem Ort - gemeinsam genutzt von Clevoriq Hub und Clevoriq Documents.",
    )

    st.markdown("## 📤 Dokumente hinzufügen")

    bibliothek_dateien = st.file_uploader(
        "Dateien auswählen",
        type=dokument_verarbeitung.SUPPORTED_EXTENSIONS,
        accept_multiple_files=True,
        key="bibliothek_uploader",
        label_visibility="collapsed",
    )
    dateien_verarbeiten(bibliothek_dateien, benutzer_id)

    st.divider()

    alle_dokumente_bibliothek = speicher.dokumente_laden(benutzer_id)
    wort = "Dokument" if len(alle_dokumente_bibliothek) == 1 else "Dokumente"
    st.markdown(f"## 📚 {len(alle_dokumente_bibliothek)} {wort} in deiner Bibliothek")

    if not alle_dokumente_bibliothek:
        komponenten.leerer_zustand("Noch keine Dokumente in der Bibliothek. Lade oben deine erste Datei hoch.")
        return

    spalte_suche, spalte_sortierung, spalte_typ = st.columns([3, 2, 2])

    suchbegriff = spalte_suche.text_input(
        "Dokumente durchsuchen",
        placeholder="🔍 Dokumente durchsuchen",
        label_visibility="collapsed",
        key="bibliothek_seite_suchbegriff",
    ).strip()

    sortierung = spalte_sortierung.selectbox(
        "Sortierung",
        options=dokumentbibliothek.SORTIERUNGEN,
        label_visibility="collapsed",
        key="bibliothek_seite_sortierung",
    )

    dateityp_filter = spalte_typ.selectbox(
        "Dateityp",
        options=dokumentbibliothek.dateitypen_optionen(alle_dokumente_bibliothek),
        format_func=lambda t: (
            t if t == dokumentbibliothek.DATEITYP_ALLE else dokumentbibliothek.dateityp_anzeige(t)
        ),
        label_visibility="collapsed",
        key="bibliothek_seite_dateityp",
    )

    spalte_datum, spalte_datumsbereich = st.columns([2, 3])

    datumsfilter = spalte_datum.selectbox(
        "Zeitraum",
        options=dokumentbibliothek.DATUMSFILTER,
        label_visibility="collapsed",
        key="bibliothek_seite_datumsfilter",
    )

    benutzerdefiniert_von = None
    benutzerdefiniert_bis = None

    if datumsfilter == dokumentbibliothek.DATUMSFILTER_BENUTZERDEFINIERT:
        heute = datetime.now().date()
        datumsbereich = spalte_datumsbereich.date_input(
            "Zeitraum wählen",
            value=(heute - timedelta(days=7), heute),
            label_visibility="collapsed",
            key="bibliothek_seite_datumsbereich",
        )
        if isinstance(datumsbereich, tuple) and len(datumsbereich) == 2:
            benutzerdefiniert_von, benutzerdefiniert_bis = datumsbereich

    gefilterte_dokumente = dokumentbibliothek.dokumente_filtern(
        alle_dokumente_bibliothek,
        suchbegriff=suchbegriff,
        datumsfilter=datumsfilter,
        benutzerdefiniert_von=benutzerdefiniert_von,
        benutzerdefiniert_bis=benutzerdefiniert_bis,
        dateityp_filter=dateityp_filter,
    )
    angezeigte_dokumente = dokumentbibliothek.dokumente_sortieren(
        gefilterte_dokumente, sortierung
    )

    gefiltert_aktiv = (
        suchbegriff
        or datumsfilter != dokumentbibliothek.DATUMSFILTER_ALLE
        or dateityp_filter != dokumentbibliothek.DATEITYP_ALLE
    )

    if gefiltert_aktiv:
        st.caption(
            f"{len(angezeigte_dokumente)} von {len(alle_dokumente_bibliothek)} "
            "Dokumenten angezeigt"
        )

    if not angezeigte_dokumente:
        komponenten.leerer_zustand("Keine Dokumente gefunden.")
        return

    spalten = st.columns(2)

    for index, dokument in enumerate(angezeigte_dokumente):
        dokument_id = dokument["id"]
        hochgeladen_am = datetime.fromisoformat(
            dokument["hochgeladen_am"]
        ).strftime("%d.%m.%Y")
        groesse = dokumentbibliothek.groesse_text(dokument)

        with spalten[index % 2].container(border=True, key=f"bibliothek_karte_{dokument_id}"):
            umbenennen_aktiv_key = f"bibliothek_umbenennen_aktiv_{dokument_id}"

            if st.session_state.get(umbenennen_aktiv_key):
                with st.form(key=f"bibliothek_umbenennen_formular_{dokument_id}"):
                    neuer_name = st.text_input(
                        "Neuer Dateiname", value=dokument["dateiname"], label_visibility="collapsed"
                    )
                    spalte_speichern, spalte_abbrechen = st.columns(2)
                    gespeichert = spalte_speichern.form_submit_button(
                        "Speichern", type="primary", use_container_width=True
                    )
                    abgebrochen = spalte_abbrechen.form_submit_button(
                        "Abbrechen", use_container_width=True
                    )

                if gespeichert:
                    if speicher.dokument_umbenennen(dokument_id, benutzer_id, neuer_name):
                        st.session_state[umbenennen_aktiv_key] = False
                        st.rerun()
                    else:
                        st.error("Bitte gib einen gültigen Dateinamen ein.")

                if abgebrochen:
                    st.session_state[umbenennen_aktiv_key] = False
                    st.rerun()

                continue

            st.markdown(f"**{dokument['dateiname']}**")
            st.caption(
                dokumentbibliothek.dateityp_anzeige(dokument.get("dateityp", "pdf"))
                + " · "
                + dokumentbibliothek.einheiten_text(dokument)
            )

            zusatz = f"Hochgeladen am {hochgeladen_am}"
            if groesse:
                zusatz += f" · {groesse}"
            st.caption(zusatz)
            st.caption("✅ Gespeichert")

            spalte_umbenennen, spalte_loeschen = st.columns(2)

            if spalte_umbenennen.button(
                "✏️ Umbenennen",
                key=f"bibliothek_umbenennen_{dokument_id}",
                use_container_width=True,
            ):
                st.session_state[umbenennen_aktiv_key] = True
                st.rerun()

            with spalte_loeschen.popover("🗑 Löschen", use_container_width=True):
                st.write(f"„{dokument['dateiname']}“ entfernen?")
                st.caption(
                    "Löscht auch alle gespeicherten Textausschnitte und "
                    "entfernt das Dokument aus allen Chats."
                )
                if st.button(
                    "Endgültig löschen",
                    key=f"bibliothek_confirm_del_{dokument_id}",
                    type="secondary",
                    use_container_width=True,
                ):
                    speicher.dokument_loeschen(dokument_id, benutzer_id)
                    st.rerun()
