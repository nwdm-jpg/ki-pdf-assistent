"""Wiederverwendbare Streamlit-UI-Bausteine für web_app.py.

Bündelt Darstellung, die in mehreren Bereichen (Startseite, Analyse &
Vergleich, Dokument prüfen, ggf. künftige Bereiche) identisch aussehen
soll - Seitenköpfe, leere Zustände, Ergebniskarten, Rückfragen-Chat,
Quellenangaben, große Startseiten-Karten - damit web_app.py nicht
dieselbe Streamlit-Auszeichnung mehrfach dupliziert und das
Erscheinungsbild der App garantiert konsistent bleibt (ein Ort für
Layout-/Formatierungsentscheidungen statt verstreuter Kopien).

Farben, Radien und Schriftarten kommen bewusst aus dem nativen
Streamlit-Theme (`.streamlit/config.toml`), nicht aus CSS - hier steckt
nur die wenige strukturelle CSS-Ergänzung, die config.toml nicht
abdecken kann (z. B. die Mindesthöhe der großen Startseiten-Karten).
"""

import streamlit as st


# Nur strukturelle Ergänzungen (Layoutbreite, Kartengröße), keine
# Farben/Schriftarten - die kommen aus .streamlit/config.toml.
_CSS = """
<style>
.block-container {
    max-width: 1000px;
    padding-top: 2.5rem;
    padding-bottom: 3rem;
}
[data-testid="stSidebar"] .block-container {
    padding-top: 2rem;
}
h1, h2, h3 {
    letter-spacing: -0.01em;
}

/* Große, prominente Modus-Karten auf der Startseite */
[class*="st-key-home_karte_"] {
    padding: 1.5rem 1.25rem 1.25rem 1.25rem;
    min-height: 240px;
}
[class*="st-key-home_karte_"] h2 {
    font-size: 2.75rem;
    text-align: center;
    margin-bottom: 0.25rem;
}
[class*="st-key-home_karte_"] h3 {
    text-align: center;
    margin-top: 0;
}
[class*="st-key-home_karte_"] p {
    text-align: center;
}

/* Sidebar-Navigation: "Startseite" etwas größer/prominenter als die
   übrigen Bereiche, aber weiterhin derselbe Button-Typ (nur größer). */
[class*="st-key-nav_gross_"] button {
    font-size: 1.05rem;
    font-weight: 600;
    padding-top: 0.7rem;
    padding-bottom: 0.7rem;
}
[class*="st-key-nav_gross_"] {
    margin-bottom: 0.5rem;
}
</style>
"""


def css_einbinden():
    """Bindet die zentrale, projektweite CSS-Ergänzung einmalig ein."""
    st.html(_CSS)


def seiten_kopf(titel, untertitel=None):
    """Einheitlicher Seitentitel + optionaler, knapper Untertitel."""
    st.title(titel)

    if untertitel:
        st.caption(untertitel)


def leerer_zustand(text):
    """Einheitlich formatierter Hinweis für leere Zustände."""
    st.info(text)


def quellen_hinweis(quellenhinweis):
    """Einheitliche Darstellung eines Quellenhinweises (falls vorhanden)."""
    if quellenhinweis:
        st.caption(quellenhinweis)


def modus_karte(icon, titel, beschreibung, button_label, key, deaktiviert=False, deaktiviert_hinweis=None):
    """Kompakte Aktions-Karte (z. B. eine Analyse-/Prüfkategorie).

    Gibt True zurück, wenn der Button in diesem Lauf geklickt wurde.
    """
    with st.container(border=True):
        st.markdown(f"**{icon} {titel}**")
        st.caption(beschreibung)

        geklickt = st.button(
            button_label, key=key, use_container_width=True, disabled=deaktiviert
        )

        if deaktiviert and deaktiviert_hinweis:
            st.caption(deaktiviert_hinweis)

    return geklickt


def nav_eintrag(label, aktiv, key, gross=False):
    """Ein einzelner Eintrag der Sidebar-Hauptnavigation.

    Zeigt den aktiven Bereich über den Button-Typ an (primary/secondary)
    - dieselbe Logik, die bereits für die Chat-Liste genutzt wird, damit
    kein zweites Auswahl-Konzept entsteht. `gross=True` (z. B. für
    "Startseite") macht den Eintrag über eine `key`-basierte CSS-Klasse
    sichtbar größer/prominenter als die übrigen, bleibt aber derselbe
    Button-Typ - keine separate Mini-Optik.

    Gibt True zurück, wenn der Eintrag in diesem Lauf angeklickt wurde.
    """
    container_key = f"nav_gross_{key}" if gross else f"nav_{key}"

    with st.container(key=container_key):
        geklickt = st.button(
            label,
            key=f"nav_button_{key}",
            use_container_width=True,
            type="primary" if aktiv else "secondary",
        )

    return geklickt


def start_karte(icon, titel, beschreibung, button_label, key):
    """Große, prominente Modus-Karte für die Startseite.

    Deutlich größer/auffälliger als `modus_karte` (Startseite braucht
    eine klare, einladende Auswahl statt kompakter Werkzeugkacheln).
    Gibt True zurück, wenn der Button in diesem Lauf geklickt wurde.
    """
    with st.container(border=True, key=f"home_karte_{key}"):
        st.markdown(f"## {icon}")
        st.markdown(f"### {titel}")
        st.write(beschreibung)
        geklickt = st.button(
            button_label,
            key=f"home_button_{key}",
            use_container_width=True,
            type="primary",
        )

    return geklickt


def dokument_mehrfachauswahl(label, session_key, widget_key, dokumente, hilfetext=None):
    """Mehrfachauswahl von Dokumenten mit robuster, bereichsunabhängiger Persistenz.

    Hält die Auswahl zusätzlich in einer eigenen Session-State-Variable
    (`session_key`), die unabhängig vom Widget-Key (`widget_key`)
    überlebt, auch wenn das Multiselect in einem Skriptlauf nicht
    gerendert wird (z. B. weil gerade ein anderer Bereich aktiv ist) -
    Streamlit würde den reinen Widget-State sonst verwerfen, sobald ein
    Widget einen Lauf lang nicht gezeichnet wird. Wird von Analyse &
    Vergleich UND Dokument prüfen genutzt, mit jeweils eigenem
    `session_key`/`widget_key`, damit die beiden Auswahlen unabhängig
    bleiben.

    Gibt die aktuell ausgewählte Liste von Dokument-IDs zurück.
    """
    namen_je_id = {dokument["id"]: dokument["dateiname"] for dokument in dokumente}
    verfuegbare_ids = list(namen_je_id.keys())

    if session_key not in st.session_state:
        st.session_state[session_key] = []

    st.session_state[session_key] = [
        i for i in st.session_state[session_key] if i in verfuegbare_ids
    ]

    ausgewaehlt = st.multiselect(
        label,
        options=verfuegbare_ids,
        default=st.session_state[session_key],
        format_func=lambda i: namen_je_id.get(i, str(i)),
        key=widget_key,
        help=hilfetext,
    )

    st.session_state[session_key] = ausgewaehlt

    return ausgewaehlt


def ergebnis_kopf(icon, titel, dokument_namen, reset_key, reset_label="🗑️ Ergebnis leeren"):
    """Rendert Icon+Titel, Dokumentliste und einen Reset-Button.

    Gibt True zurück, wenn der Reset-Button geklickt wurde - der
    Aufrufer entscheidet, welchen Session-State-Key er dafür leert
    (Analyse- und Prüfungsergebnis liegen in getrennten Keys).
    """
    kopf_spalte, reset_spalte = st.columns([5, 2])
    kopf_spalte.markdown(f"### {icon} {titel}")

    zurueckgesetzt = reset_spalte.button(
        reset_label, key=reset_key, use_container_width=True
    )

    st.caption(f"Dokumente: {dokument_namen}")

    return zurueckgesetzt


def rueckfragen_chat(ergebnis, ergebnis_session_key, rueckfrage_funktion, platzhalter, ueberschrift="💬 Rückfragen"):
    """Rendert den bisherigen Rückfragen-Verlauf + Eingabefeld darunter.

    `ergebnis` ist das Ergebnis-Dict aus st.session_state[ergebnis_session_key]
    (muss eine Liste unter "rueckfragen" enthalten). `rueckfrage_funktion`
    wird als `rueckfrage_funktion(ergebnis, frage)` aufgerufen und muss
    ein Dict {"text", "quellenhinweis"} liefern. Persistiert neue Runden
    direkt in `ergebnis["rueckfragen"]` und in st.session_state.
    """
    st.divider()
    st.markdown(f"#### {ueberschrift}")

    for eintrag in ergebnis["rueckfragen"]:
        with st.chat_message("user"):
            st.write(eintrag["frage"])

        with st.chat_message("assistant"):
            st.write(eintrag["antwort"])
            quellen_hinweis(eintrag["quellenhinweis"])

    frage = st.chat_input(platzhalter)

    if not frage:
        return

    with st.chat_message("user"):
        st.write(frage)

    try:
        with st.chat_message("assistant"):
            with st.spinner("Antwort wird erstellt..."):
                antwort = rueckfrage_funktion(ergebnis, frage)

            st.write(antwort["text"])
            quellen_hinweis(antwort["quellenhinweis"])

        ergebnis["rueckfragen"].append(
            {
                "frage": frage,
                "antwort": antwort["text"],
                "quellenhinweis": antwort["quellenhinweis"],
            }
        )
        st.session_state[ergebnis_session_key] = ergebnis

        st.rerun()

    except Exception as fehler:
        st.error("Die Rückfrage ist fehlgeschlagen.")
        st.caption(f"Technische Details: {fehler}")
