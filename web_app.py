import streamlit as st
from pypdf import PdfReader

from pdf_logik import (
    formatiere_quellenhinweis,
    frage_beantworten,
    pdf_seiten_extrahieren,
    relevante_seiten_ermitteln,
    relevanten_text_zusammenstellen,
    verwendete_quellen,
)


st.set_page_config(
    page_title="KI-PDF-Assistent",
    page_icon="📄",
    layout="wide",
)

st.markdown(
    """
    <style>
    .block-container {
        max-width: 900px;
        padding-top: 2.5rem;
        padding-bottom: 3rem;
    }
    [data-testid="stChatMessage"] {
        border-radius: 14px;
    }
    [data-testid="stSidebar"] .block-container {
        padding-top: 2rem;
    }
    h1, h2, h3 {
        letter-spacing: -0.01em;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


if "chat_verlauf" not in st.session_state:
    st.session_state.chat_verlauf = []

if "dokumente" not in st.session_state:
    st.session_state.dokumente = {}


with st.sidebar:
    st.markdown("## 📄 KI-PDF-Assistent")
    st.caption("Lade ein oder mehrere PDFs hoch und stelle Fragen dazu.")

    hochgeladene_dateien = st.file_uploader(
        "PDF-Dateien hinzufügen",
        type=["pdf"],
        accept_multiple_files=True,
    )

    aktuelle_schluessel = set()

    for datei in hochgeladene_dateien or []:
        schluessel = (datei.name, datei.size)
        aktuelle_schluessel.add(schluessel)

        if schluessel not in st.session_state.dokumente:
            try:
                reader = PdfReader(datei)
            except Exception as fehler:
                st.error(f"„{datei.name}“ konnte nicht gelesen werden.")
                st.caption(f"Technische Details: {fehler}")
                continue

            seiten = pdf_seiten_extrahieren(reader, datei.name)
            st.session_state.dokumente[schluessel] = {
                "name": datei.name,
                "seiten": seiten,
            }

    # Dokumente entfernen, die nicht mehr im Uploader ausgewählt sind.
    st.session_state.dokumente = {
        schluessel: dokument
        for schluessel, dokument in st.session_state.dokumente.items()
        if schluessel in aktuelle_schluessel
    }

    if st.session_state.dokumente:
        st.divider()
        st.markdown(f"#### Dokumente ({len(st.session_state.dokumente)})")

        for dokument in st.session_state.dokumente.values():
            seiten = dokument["seiten"]
            anzahl_zeichen = sum(len(eintrag["text"]) for eintrag in seiten)

            with st.container(border=True):
                st.markdown(f"**📄 {dokument['name']}**")
                seiten_wort = "Seite" if len(seiten) == 1 else "Seiten"
                st.caption(f"{len(seiten)} {seiten_wort} · {anzahl_zeichen} Zeichen")

                with st.expander("Extrahierten Text anzeigen"):
                    voller_text = "\n".join(
                        f"\n--- Seite {eintrag['seitennummer']} ---\n"
                        f"{eintrag['text']}"
                        for eintrag in seiten
                    )
                    st.text(voller_text)

    st.divider()

    if st.button(
        "🗑️ Chat leeren",
        use_container_width=True,
        disabled=not st.session_state.chat_verlauf,
    ):
        st.session_state.chat_verlauf = []
        st.rerun()


st.title("KI-PDF-Assistent")

if not st.session_state.dokumente:
    st.info("Lade links mindestens ein PDF hoch, um Fragen stellen zu können.")
else:
    st.caption(
        "Stelle Fragen zu deinen hochgeladenen Dokumenten – auch "
        "Rückfragen zum bisherigen Gespräch sind möglich."
    )

    for eintrag in st.session_state.chat_verlauf:
        with st.chat_message("user"):
            st.write(eintrag["frage"])

        with st.chat_message("assistant"):
            st.write(eintrag["antwort"])

            quellenhinweis = formatiere_quellenhinweis(eintrag.get("quellen", []))

            if quellenhinweis:
                st.caption(quellenhinweis)

    frage = st.chat_input("Stelle eine Frage zu deinen PDFs...")

    if frage:
        with st.chat_message("user"):
            st.write(frage)

        alle_seiten = [
            eintrag
            for dokument in st.session_state.dokumente.values()
            for eintrag in dokument["seiten"]
        ]

        # Die letzten Chatrunden fließen zusätzlich in die Seitensuche
        # ein, damit Rückfragen wie "Und wie ist das im zweiten Vertrag?"
        # noch die passenden Seiten finden.
        zusatzkontext = "\n".join(
            f"{eintrag['frage']} {eintrag['antwort']}"
            for eintrag in st.session_state.chat_verlauf[-2:]
        )

        # Pro Dokument mehrere Seiten zulassen, aber bei vielen Dokumenten
        # die Gesamtzahl der Auszüge im Prompt begrenzen. Jedes Dokument
        # erhält so mindestens eine Chance, im Kontext zu erscheinen.
        anzahl_dokumente = len(st.session_state.dokumente)
        anzahl_pro_dokument = (
            3 if anzahl_dokumente == 1 else max(1, 6 // anzahl_dokumente)
        )

        beste_seiten = relevante_seiten_ermitteln(
            frage,
            alle_seiten,
            anzahl=anzahl_pro_dokument,
            zusatzkontext=zusatzkontext,
        )
        relevanter_text = relevanten_text_zusammenstellen(beste_seiten)
        ausgewaehlte_quellen = verwendete_quellen(beste_seiten)

        try:
            with st.chat_message("assistant"):
                with st.spinner("Die KI durchsucht deine Dokumente..."):
                    antwort_text = frage_beantworten(
                        frage,
                        relevanter_text,
                        verlauf=st.session_state.chat_verlauf[-6:],
                    )

            st.session_state.chat_verlauf.append(
                {
                    "frage": frage,
                    "antwort": antwort_text,
                    "quellen": ausgewaehlte_quellen,
                }
            )

            st.rerun()

        except Exception as fehler:
            st.error("Die KI-Anfrage ist fehlgeschlagen.")
            st.caption(f"Technische Details: {fehler}")
