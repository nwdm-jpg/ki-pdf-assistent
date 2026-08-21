"""Streamlit-Oberfläche des "Clevoriq Hub" - der zentralen Startseite

nach dem Login (siehe CLAUDE.md "Clevoriq Account & Hub").

Zeigt die "Meine Produkte"-Übersicht (aktuell nur Clevoriq Documents,
datengetrieben aus `produkte.PRODUKTE` + `speicher.produkt_zugriffe` -
siehe CLAUDE.md "Produktsystem") sowie Kurz-Einstiege in die zentrale
Bibliothek und Konto & Sicherheit. Enthält selbst KEINE
Navigations-/Sitzungslogik (welcher Bereich als Nächstes gerendert
wird, entscheidet ausschließlich `web_app.py`) - diese Funktion gibt
nur zurück, WAS der Benutzer in diesem Lauf angeklickt hat.
"""

import streamlit as st

import komponenten
import produkte
import speicher


# Rückgabewerte von `seite()` - `web_app.py` übersetzt sie in einen
# Bereichs-/Kontextwechsel und prüft den Produktzugriff dabei serverseitig
# ERNEUT (siehe CLAUDE.md "deny by default") statt sich allein auf die
# hier bereits deaktivierte Schaltfläche zu verlassen.
AKTION_DOKUMENTE_OEFFNEN = "documents_oeffnen"
AKTION_BIBLIOTHEK = "bibliothek"
AKTION_KONTO = "konto"


def _produkt_karte(benutzer_id, product_key, info):
    aktiv = speicher.produkt_zugriff_aktiv(benutzer_id, product_key)

    with st.container(border=True, key=f"hub_produkt_{product_key}"):
        st.markdown(f"## {info['icon']}")
        st.markdown(f"### {info['name']}")
        st.write(info["beschreibung"])
        st.caption(f"Status: {'🟢 Aktiv' if aktiv else '⚪ Kein Zugriff'}")

        geklickt = st.button(
            "Öffnen",
            key=f"hub_produkt_oeffnen_{product_key}",
            use_container_width=True,
            type="primary",
            disabled=not aktiv,
        )

    return geklickt and aktiv


def _weitere_produkte_karte():
    with st.container(border=True, key="hub_weitere_produkte"):
        st.markdown("## ➕")
        st.markdown("### Weitere Produkte")
        st.write("Demnächst verfügbar.")
        st.caption("Status: ⚪ Demnächst verfügbar")


def seite(benutzer_id):
    """Rendert den Clevoriq Hub. Gibt eine der `AKTION_*`-Konstanten
    zurück, wenn der Benutzer in diesem Lauf navigieren möchte, sonst
    `None`."""
    komponenten.hero_glow()
    komponenten.marke_kopf(gross=True, produkt=None)
    st.markdown("### Deine zentrale Plattform für alle Clevoriq-Produkte.")

    alle_dokumente = speicher.dokumente_laden(benutzer_id)

    spalte_stat_dokumente, spalte_stat_produkte = st.columns(2)
    spalte_stat_dokumente.metric("Dokumente in deiner Library", len(alle_dokumente))
    spalte_stat_produkte.metric(
        "Aktive Produkte",
        sum(
            1
            for key in produkte.PRODUKTE
            if speicher.produkt_zugriff_aktiv(benutzer_id, key)
        ),
    )

    st.divider()
    st.markdown("## 🧩 Meine Produkte")

    produkt_keys = list(produkte.PRODUKTE.keys())
    spalten = st.columns(len(produkt_keys) + 1)
    aktion = None

    for spalte, product_key in zip(spalten, produkt_keys):
        with spalte:
            if _produkt_karte(benutzer_id, product_key, produkte.PRODUKTE[product_key]):
                aktion = AKTION_DOKUMENTE_OEFFNEN if product_key == produkte.PRODUKT_DOCUMENTS else None

    with spalten[-1]:
        _weitere_produkte_karte()

    st.divider()
    st.markdown("## Schnellzugriff")

    spalte_bibliothek, spalte_konto = st.columns(2)

    with spalte_bibliothek:
        if komponenten.start_karte(
            "📚",
            "Zentrale Bibliothek",
            "Alle deine Dokumente, produktübergreifend an einem Ort.",
            "Zur Bibliothek",
            key="hub_bibliothek",
        ):
            aktion = AKTION_BIBLIOTHEK

    with spalte_konto:
        if komponenten.start_karte(
            "⚙",
            "Konto & Sicherheit",
            "Profil, Passwort, 2FA und Datenschutz verwalten.",
            "Konto öffnen",
            key="hub_konto",
        ):
            aktion = AKTION_KONTO

    return aktion
