"""Wiederverwendbare Streamlit-UI-Bausteine für web_app.py.

Bündelt Darstellung, die in mehreren Bereichen (Startseite, Analyse &
Vergleich, Dokument prüfen, ggf. künftige Bereiche) identisch aussehen
soll - Seitenköpfe, leere Zustände, Ergebniskarten, Rückfragen-Chat,
Quellenangaben, große Startseiten-Karten, die AVENLOQ-Wortmarke - damit
web_app.py nicht dieselbe Streamlit-Auszeichnung mehrfach dupliziert und
das Erscheinungsbild der App garantiert konsistent bleibt (ein Ort für
Layout-/Formatierungs-/Markenentscheidungen statt verstreuter Kopien).

Grundfarben, Radien und die Sidebar-/Content-Aufteilung kommen bewusst
aus dem nativen Streamlit-Theme (`.streamlit/config.toml`, inkl. der
AVENLOQ-Markenpalette), nicht aus CSS. Hier steckt nur, was config.toml
nicht abdecken kann: die AVENLOQ-Design-Tokens (Farbverlauf-Variable,
Schriftart), der Blue-→-Violet-Farbverlauf für Primär-Buttons/aktive
Navigation/Highlights, die Wortmarke und wenige strukturelle
Ergänzungen (z. B. Mindesthöhe der großen Startseiten-Karten).
"""

import streamlit as st


# Zentrale AVENLOQ-Design-Tokens (Farben/Farbverlauf/Schriftart) +
# strukturelle Ergänzungen (Layoutbreite, Kartengröße, Wortmarke,
# Button-Hierarchie), die über das native Theme (.streamlit/config.toml)
# hinausgehen. Der Blue-→-Violet-Farbverlauf ist bewusst auf
# Primär-Buttons, aktive Navigation und wenige Highlights (Wortmarke,
# Tagline) beschränkt - normale UI-Elemente bleiben zurückhaltend/neutral.
_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

:root {
    --avq-navy: #0D1026;
    --avq-blue: #2563EB;
    --avq-violet: #7C3AED;
    --avq-purple: #A855F7;
    --avq-lavender: #EDE9FE;
    --avq-gradient: linear-gradient(135deg, var(--avq-blue) 0%, var(--avq-violet) 100%);
}

html, body, [class*="css"] {
    font-family: "Inter", "Segoe UI", sans-serif;
}

/* Streamlits fixe Kopfzeile (Deploy-/Menü-Leiste) ist absolut
   positioniert, 60px hoch und deckend (siehe [data-testid="stHeader"]),
   und liegt über dem Content-Bereich statt Platz für ihn zu reservieren.
   padding-top MUSS diese 60px überschreiten, sonst rendert der Anfang
   des Inhalts (insbesondere die große Wortmarke auf der Startseite)
   unter der Kopfzeile und wird von ihr überdeckt/"abgeschnitten". */
.block-container {
    max-width: 1000px;
    padding-top: 4.75rem;
    padding-bottom: 3rem;
}
[data-testid="stSidebar"] .block-container {
    padding-top: 2rem;
}
h1, h2, h3 {
    letter-spacing: -0.01em;
}

/* AVENLOQ-Wortmarke (Icon + Schriftzug + optionaler, dezenter
   Produkt-Indikator darunter, z. B. "Documents"), siehe marke_kopf().
   Keine eigene Textfarbe - sie erbt bewusst die Umgebungsfarbe (helle
   Sidebar-Schrift auf Navy vs. dunkle Schrift im hellen Content-Bereich).
   AVENLOQ ist die dominante Plattform-/Konto-Marke, der Produkt-Name
   (aktuell nur "Documents") steht bewusst kleiner/zurückhaltender
   darunter statt gleichrangig daneben - siehe CLAUDE.md
   "Platform & Product Branding". */
.avq-marke {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    margin-bottom: 0.25rem;
}
.avq-marke-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    width: 2rem;
    height: 2rem;
    border-radius: 0.55rem;
    background: var(--avq-gradient);
    color: #FFFFFF !important;
    font-weight: 800;
    font-size: 1.05rem;
    line-height: 1;
}
.avq-marke-wortmarke {
    display: flex;
    flex-direction: column;
    gap: 0.1rem;
    line-height: 1;
}
.avq-marke-text {
    font-weight: 800;
    letter-spacing: 0.06em;
    font-size: 1.15rem;
    text-transform: uppercase;
    line-height: 1;
}
.avq-marke-produkt {
    font-weight: 600;
    letter-spacing: 0.04em;
    font-size: 0.65rem;
    text-transform: uppercase;
    opacity: 0.65;
    line-height: 1;
}
.avq-marke--gross .avq-marke-icon {
    width: 3.25rem;
    height: 3.25rem;
    border-radius: 0.85rem;
    font-size: 1.75rem;
}
.avq-marke--gross .avq-marke-text {
    font-size: 2rem;
}
.avq-marke--gross .avq-marke-produkt {
    font-size: 0.95rem;
}

/* Tagline auf der Startseite - einer der wenigen bewussten
   Farbverlauf-Akzente (Highlight), siehe marke_tagline(). */
.avq-tagline {
    font-size: 1.35rem;
    font-weight: 600;
    line-height: 1.3;
    margin: 0.9rem 0 0.4rem 0;
    background: var(--avq-gradient);
    -webkit-background-clip: text;
    background-clip: text;
    color: transparent;
}

/* Zurückhaltender, professioneller Hinweis-Baustein (z. B. Disclaimer
   in Analyse & Vergleich / Dokument prüfen) statt einer auffälligen
   Warnbox, siehe hinweis_dezent(). */
.avq-hinweis {
    font-size: 0.85rem;
    color: #52525B;
    background: var(--avq-lavender);
    border-left: 3px solid var(--avq-violet);
    border-radius: 8px;
    padding: 0.55rem 0.85rem;
    margin: 0.35rem 0 1rem 0;
}

/* Primär-Buttons (inkl. aktiver Navigation) erhalten den
   AVENLOQ-Farbverlauf statt einer einfarbigen Füllung - der einzige
   Ort im UI, an dem Buttons den Verlauf tragen (Button-Hierarchie).
   Eine einzige zentrale Regel für alle Primär-Buttons (Startseite,
   Analyse & Vergleich, Dokument-prüfen-Kategorien, "Kompletten
   Dokumenten-Check starten", aktive Navigation) statt Einzelstyling je
   Seite.

   WICHTIG: Streamlit rendert das Button-Label nicht direkt im
   Button-Element - es steckt vier Ebenen tief in einem Absatz
   (p-Tag) innerhalb von [data-testid="stMarkdownContainer"], und
   genau dieser Absatz trägt Streamlits eigene Absatz-Schriftgröße
   (14px), die NICHT von einer font-size-Regel auf dem äußeren
   Button-Element geerbt/überschrieben wird - daher blieb der
   Button-Text bei einer reinen button[kind=primary]-Regel klein.

   Der zweite, hartnäckigere Fehler (Text saß sichtbar am oberen statt
   im mittleren Bereich des Buttons) lag NICHT an fehlendem
   flex-Centering, sondern an einer echten Selektor-Kollision: die
   Regel "[class*='st-key-home_karte_'] p { min-height: 5rem }" weiter
   unten (für die Kartenbeschreibung auf der Startseite) ist ein reiner
   Nachfahren-Selektor und traf dadurch JEDEN Absatz innerhalb der
   Karte - auch den Absatz im eigenen Button. Das erzwang eine
   Mindesthöhe von 80px auf dem Button-Label, in der der Text als
   Blockelement oben zu sitzen kam. Erst per Playwright nachgemessen
   (nicht nur vermutet) wurde klar, dass weder align-items/align-self
   noch eine reine height-Regel das beheben - min-height gewinnt gegen
   eine kleinere berechnete Höhe. Fix: min-height auf dem Button-Absatz
   explizit mit !important auf 0 zurücksetzen, zusätzlich zu Höhe und
   align-self als Absicherung.

   Hinweis für künftige Änderungen an diesem CSS-Block: `_CSS` geht
   durch `st.html()` (DOMPurify) - Text, der wie ein HTML-Tag aussieht
   (spitze Klammern um ein Wort, z. B. beim Nennen eines Elementnamens),
   darf hier auch innerhalb von Kommentaren nicht vorkommen, sonst wird
   das umgebende Style-Element beschädigt (siehe Git-Historie:
   "AAVENLOQ"-Bug). Elementnamen in Kommentaren immer ohne spitze
   Klammern schreiben (z. B. "Style-Element" statt der Tag-Schreibweise). */
button[kind="primary"],
[data-testid="stBaseButton-primary"] {
    background: var(--avq-gradient) !important;
    border: none !important;
    color: #FFFFFF !important;
    display: flex;
    align-items: center;
    justify-content: center;
    text-align: center;
}
button[kind="primary"] [data-testid="stMarkdownContainer"],
[data-testid="stBaseButton-primary"] [data-testid="stMarkdownContainer"] {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    width: 100%;
}
button[kind="primary"] [data-testid="stMarkdownContainer"] p,
[data-testid="stBaseButton-primary"] [data-testid="stMarkdownContainer"] p {
    align-self: center !important;
    height: auto !important;
    min-height: 0 !important;
    font-size: 17px;
    font-weight: 600;
    line-height: 1;
    margin: 0;
    text-align: center;
}
button[kind="primary"]:hover,
[data-testid="stBaseButton-primary"]:hover {
    filter: brightness(1.08);
    color: #FFFFFF !important;
}

/* Einmalige Ausnahme NUR für den Startseiten-Button "Dokument prüfen"
   (Karte "pruefung", siehe start_karte-Aufruf in web_app.py): das
   Label enthält einen echten Zeilenumbruch nach dem Bindestrich
   ("Dokumenten-\nprüfer"), damit der Umbruch garantiert genau dort
   erzwungen wird statt dem Browser die Umbruchstelle zu überlassen.
   Der Absatz übernimmt normalerweise "white-space: normal" (Umbrüche
   werden zu Leerzeichen), daher hier gezielt auf "pre-line"
   umgeschaltet, aber nur für diesen einen Button (Selektor über den
   Button-eigenen Key "home_button_pruefung"), damit kein anderer
   Button betroffen ist. line-height leicht reduziert, damit beide
   Zeilen bei 17px Schrift noch innerhalb der unveränderten
   Button-Höhe (40px) Platz finden. */
[class*="st-key-home_button_pruefung"] button p {
    white-space: pre-line !important;
    line-height: 0.94 !important;
}

/* Deaktivierter Primär-Button muss trotz der obigen !important-Regel
   klar deaktiviert wirken - sonst sähe z. B. eine wegen zu weniger
   Dokumente gesperrte Analyse-Karte weiterhin wie ein aktiver
   Farbverlauf-Button aus. Greift für jeden Primär-Button app-weit
   (Startseite hat aktuell keine deaktivierbaren, daher unkritisch). */
button[kind="primary"]:disabled,
[data-testid="stBaseButton-primary"]:disabled {
    background: #E5E7EB !important;
    color: #9CA3AF !important;
    border: 1px solid #E5E7EB !important;
    filter: none;
    cursor: not-allowed;
}

/* Destruktive Aktion (Dokument endgültig löschen) - eigene, dezente
   "Danger"-Optik statt des Primär-Farbverlaufs, damit "wichtig" und
   "gefährlich" visuell nicht verwechselt werden. */
[class*="st-key-bibliothek_confirm_del_"] button {
    background: #FFFFFF !important;
    color: #DC2626 !important;
    border: 1px solid #FCA5A5 !important;
}
[class*="st-key-bibliothek_confirm_del_"] button:hover {
    background: #FEF2F2 !important;
    border-color: #DC2626 !important;
    color: #B91C1C !important;
}

/* Zweite destruktive Aktion (Konto endgültig löschen, siehe `konto.py`) -
   derselbe dezente "Danger"-Optik-Hook, nur mit eigenem Key-Präfix statt
   den dokumentspezifischen `bibliothek_confirm_del_`-Hook zweckzuentfremden. */
[class*="st-key-konto_confirm_del_"] button {
    background: #FFFFFF !important;
    color: #DC2626 !important;
    border: 1px solid #FCA5A5 !important;
}
[class*="st-key-konto_confirm_del_"] button:hover {
    background: #FEF2F2 !important;
    border-color: #DC2626 !important;
    color: #B91C1C !important;
}

/* Große, prominente Modus-Karten auf der Startseite. Titel- und
   Beschreibungszeile reservieren eine Mindesthöhe, die dem tatsächlich
   gerenderten Zweizeiler (Titel) bzw. Dreizeiler (Beschreibung) bei
   dieser Kartenbreite entspricht (per Playwright nachgemessen: 95px /
   77px) - nicht nur eine grobe Schätzung. Da kein Kartentext diese
   Reservierung überschreitet, werden alle vier Karten dadurch exakt
   gleich hoch, unabhängig davon ob Titel/Beschreibung ein- oder
   mehrzeilig umbrechen; margin-top:auto auf dem Button schiebt ihn in
   jeder Karte an denselben unteren Rand. */
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
    min-height: 6.1rem;
}
[class*="st-key-home_karte_"] p {
    text-align: center;
    min-height: 5rem;
}
[class*="st-key-home_karte_"] .stButton {
    margin-top: auto;
    padding-top: 0.75rem;
}

/* Kompakte Aktions-Karten (Dokument prüfen / Analyse & Vergleich), siehe
   modus_karte(). Titel/Beschreibung reservieren eine nachgemessene
   Mindesthöhe für den jeweils längsten real vorkommenden Umbruch (Titel
   zweizeilig, Beschreibung bis zu dreizeilig bei der schmaleren
   Analyse-Kartenbreite) - dadurch werden alle Karten einer Gruppe exakt
   gleich hoch statt nur ungefähr. Die vierte Zeile (Hinweistext bei
   deaktiviertem Button) wird von modus_karte() IMMER gerendert (als
   unsichtbarer Platzhalter, wenn kein Hinweis nötig ist), damit
   Karten mit und ohne Hinweistext nicht unterschiedlich hoch werden,
   z. B. wenn in Analyse & Vergleich je nach Dokumentauswahl nur ein
   Teil der Karten deaktiviert ist. margin-top:auto auf dem Button
   schiebt ihn in jeder Karte an denselben unteren Rand. */
[class*="st-key-modus_karte_"] > [data-testid="stElementContainer"]:first-child {
    min-height: 3.25rem;
}
[class*="st-key-modus_karte_"] > [data-testid="stElementContainer"]:nth-child(2) {
    min-height: 4.3rem;
}
[class*="st-key-modus_karte_"] > [data-testid="stElementContainer"]:has(.stButton) {
    margin-top: auto;
    padding-top: 0.5rem;
}
[class*="st-key-modus_karte_"] > [data-testid="stElementContainer"]:nth-child(4) {
    min-height: 2.8rem;
}

/* Dokumentenbibliothek-Karten: Titel (Dateiname) reserviert zweizeilige
   Mindesthöhe, "Löschen"-Popover-Auslöser sitzt per margin-top:auto am
   unteren Rand - gleiche Idee wie bei den Modus-Karten oben, damit eine
   Reihe von zwei Karten nicht durch unterschiedlich lange Dateinamen
   gegeneinander versetzt wirkt. */
[class*="st-key-bibliothek_karte_"] > [data-testid="stElementContainer"]:first-child {
    min-height: 2.6rem;
}
[class*="st-key-bibliothek_karte_"] > [data-testid="stElementContainer"]:has(.stPopover) {
    margin-top: auto;
    padding-top: 0.5rem;
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

/* Kompakter Konto-/Abmelde-Bereich am Sidebar-Ende, siehe
   benutzer.konto_bereich(). Name bleibt in der nativen, dezenten
   Caption-Optik; "Konto & Sicherheit" und "Abmelden" bleiben bewusst
   der neutrale Sekundär-Button-Typ (kein Farbverlauf, siehe
   Button-Hierarchie). Randradius kommt bewusst NICHT von hier, sondern
   einheitlich vom nativen Theme (`buttonRadius` in config.toml) - beide
   Buttons sind dadurch automatisch identisch abgerundet wie jeder
   andere Button der App, ohne eigene Regel.

   Linksbündig statt zentriert (align-items: stretch statt center),
   beide Buttons über `use_container_width=True` (siehe benutzer.py) auf
   exakt dieselbe Breite wie der Container gebracht - dadurch sind beide
   Buttons zwangsläufig gleich breit UND linksbündig am selben Rand wie
   der Name darüber, statt wie zuvor an ihrer (unterschiedlichen)
   natürlichen Textbreite zentriert zu hängen. justify-content:
   flex-start + text-align: left richten Icon+Beschriftung innerhalb
   jedes Buttons links aus statt sie zu zentrieren. */
[class*="st-key-konto_bereich"] {
    display: flex;
    flex-direction: column;
    align-items: stretch;
    text-align: left;
    margin-top: 0.25rem;
    gap: 0.3rem;
}
[class*="st-key-konto_bereich"] [data-testid="stCaptionContainer"] {
    margin-bottom: 0.3rem;
    opacity: 0.75;
    text-align: left;
}
[class*="st-key-konto_bereich"] .stButton > button {
    width: 100%;
    white-space: nowrap;
    overflow: visible;
    font-size: 0.85rem;
    font-weight: 500;
    padding-left: 1.1rem;
    padding-right: 1.1rem;
    padding-top: 0.3rem;
    padding-bottom: 0.3rem;
    display: flex;
    align-items: center;
    justify-content: flex-start;
    text-align: left;
}
</style>
"""


def css_einbinden():
    """Bindet die zentrale, projektweite CSS-Ergänzung einmalig ein."""
    st.html(_CSS)


# AVENLOQ ist die zentrale Plattform-/Konto-Marke; das aktuelle Produkt
# ("Documents") ist eines von künftig mehreren AVENLOQ-Produkten auf
# demselben AVENLOQ-Konto (siehe CLAUDE.md "Platform & Product
# Branding") - als Modul-Konstante statt eines in marke_kopf()
# hartkodierten Strings, damit ein künftiges zweites Produkt (z. B.
# "Invoice"/"Vault") seinen eigenen Produkt-Namen einfach über den
# `produkt`-Parameter reinreicht, ohne diese Datei anzufassen. Es wird
# hier bewusst NUR die Konstante für das existierende Produkt vorbereitet
# - kein Produkt-Switcher, keine Platzhalter für weitere Produkte.
PRODUKT_NAME = "Documents"


def marke_kopf(gross=False, produkt=PRODUKT_NAME):
    """Rendert die AVENLOQ-Wortmarke (Farbverlauf-Icon + Schriftzug),
    mit einem optionalen, dezenten Produkt-Indikator (z. B. "Documents")
    kleiner darunter - AVENLOQ bleibt visuell die dominante Marke, das
    Produkt steht sichtbar, aber deutlich zurückhaltender (siehe
    `.avq-marke-produkt` in `_CSS`). `produkt=None` blendet die Zeile
    aus (z. B. für einen künftigen Kontext ohne Produktbezug); der
    Parameter existiert bewusst, damit ein späteres zweites AVENLOQ-
    Produkt dieselbe Funktion mit einem eigenen Namen aufrufen kann,
    statt eine eigene Wortmarken-Komponente zu bauen.

    Das Icon ist bewusst ein reines CSS-/Text-Icon (Farbverlauf-Kachel +
    "A", siehe `.avq-marke-icon` in `_CSS`) statt des inline eingebetteten
    `assets/logo_icon.svg`: `st.html` sanitisiert seinen Inhalt mit
    DOMPurify, das SVG-Elemente entfernt, wodurch das Icon unsichtbar
    bliebe. Die Wortmarke erbt ihre Textfarbe bewusst von der Umgebung
    (helle Schrift in der dunklen Sidebar, dunkle Schrift im hellen
    Content-Bereich). `assets/logo_icon.svg`/`assets/logo.svg` bleiben als
    vorbereitete Quell-Assets bestehen (z. B. für ein späteres Favicon
    oder eine gerasterte Grafik) - ein endgültiges Logo-Asset kann diese
    Funktion später ersetzen, ohne dass sich ihre Aufrufstellen ändern
    müssen.
    """
    klasse = "avq-marke avq-marke--gross" if gross else "avq-marke"
    produkt_html = f'<span class="avq-marke-produkt">{produkt}</span>' if produkt else ""
    st.html(
        f'<div class="{klasse}">'
        '<span class="avq-marke-icon">A</span>'
        '<span class="avq-marke-wortmarke">'
        '<span class="avq-marke-text">AVENLOQ</span>'
        f"{produkt_html}"
        "</span>"
        "</div>"
    )


def marke_tagline():
    """Rendert den AVENLOQ-Claim als Farbverlauf-Highlight (Startseiten-Hero)."""
    st.html(
        '<p class="avq-tagline">Dokumente verstehen.<br>'
        "Entscheidungen vereinfachen.</p>"
    )


def hinweis_dezent(text):
    """Zurückhaltender, professioneller Hinweisbaustein (z. B. Disclaimer).

    Ersetzt `st.warning` für wiederkehrende Rechtshinweise (Analyse &
    Vergleich, Dokument prüfen) - inhaltlich unverändert, aber visuell
    dezenter/professioneller statt einer auffälligen gelben Warnbox.
    """
    st.html(f'<div class="avq-hinweis">{text}</div>')


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


def modus_karte(icon, titel, beschreibung, button_label, key, deaktiviert=False, deaktiviert_hinweis=None, button_typ="secondary"):
    """Kompakte Aktions-Karte (z. B. eine Analyse-/Prüfkategorie).

    Trägt einen `st-key-modus_karte_*`-Hook (siehe `_CSS`), damit Titel
    und Beschreibung unabhängig von ihrer Zeilenzahl gleich viel Platz
    reservieren und der Button in jeder Karte einer Gruppe auf gleicher
    Höhe am unteren Rand sitzt - unabhängig davon, ob Titel/Beschreibung
    ein- oder zweizeilig umbrechen. Wird sowohl von Dokument prüfen als
    auch von Analyse & Vergleich genutzt, damit beide Kartengruppen
    automatisch konsistent bleiben.

    `button_typ` steuert nur die Button-Optik (siehe Streamlits eigenes
    `type=`) - Standard bleibt "secondary" (Dokument prüfen, unverändert),
    Analyse & Vergleich übergibt "primary" für den AVENLOQ-Farbverlauf.
    Die zentrale `button[kind="primary"]`-Regel in `_CSS` sorgt dafür,
    dass ein deaktivierter Primär-Button trotzdem klar deaktiviert
    aussieht statt fälschlich aktiv zu wirken.

    Gibt True zurück, wenn der Button in diesem Lauf geklickt wurde.
    """
    with st.container(border=True, key=f"modus_karte_{key}"):
        st.markdown(f"**{icon} {titel}**")
        st.caption(beschreibung)

        geklickt = st.button(
            button_label,
            key=key,
            use_container_width=True,
            disabled=deaktiviert,
            type=button_typ,
        )

        # Immer gerendert (auch als unsichtbarer Platzhalter), damit eine
        # Karte mit Hinweistext (deaktiviert) nicht höher wird als eine
        # Karte ohne - sonst würden z. B. in Analyse & Vergleich Karten
        # derselben Reihe je nach Dokumentauswahl unterschiedlich hoch.
        st.caption(deaktiviert_hinweis if (deaktiviert and deaktiviert_hinweis) else " ")

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
