"""Wiederverwendbare Streamlit-UI-Bausteine für web_app.py.

Bündelt Darstellung, die in mehreren Bereichen (Startseite, Analyse &
Vergleich, Dokument prüfen, ggf. künftige Bereiche) identisch aussehen
soll - Seitenköpfe, leere Zustände, Ergebniskarten, Rückfragen-Chat,
Quellenangaben, große Startseiten-Karten, die Clevoriq-Wortmarke - damit
web_app.py nicht dieselbe Streamlit-Auszeichnung mehrfach dupliziert und
das Erscheinungsbild der App garantiert konsistent bleibt (ein Ort für
Layout-/Formatierungs-/Markenentscheidungen statt verstreuter Kopien).

Grundfarben, Radien und die Sidebar-/Content-Aufteilung kommen bewusst
aus dem nativen Streamlit-Theme (`.streamlit/config.toml`, inkl. der
Clevoriq-Markenpalette), nicht aus CSS. Hier steckt der Rest des
Clevoriq-Design-Systems, den config.toml nicht abdecken kann: die
zentralen Design-Tokens (Farben, Schatten, Radien, Animation-Timing),
das einheitliche Blau für Primär-Buttons/aktive Navigation, das EINE
helle Hero-Band für alle Bereichs-Kopfzeilen (`seiten_hero`), das
moderne Karten-/Hover-System, die Wortmarke und wenige strukturelle
Ergänzungen (z. B. Mindesthöhe der großen Startseiten-Karten). Der
Hauptbereich hat bewusst einen reinen weißen Hintergrund ohne
Verlauf/Deko-Ebene - eine frühere mehrschichtige animierte Mesh-Deko
wurde zurückgebaut, weil sie der geforderten Design-Einheitlichkeit
entgegenstand (siehe Kommentare bei den betroffenen Regeln).
"""

import html

import streamlit as st


# Zentrale Clevoriq-Design-Tokens (Farben/Schatten/Radien/Animation-
# Timing/Schriftart) + strukturelle Ergänzungen (Layoutbreite,
# Kartengröße, Wortmarke, Button-Hierarchie), die über das native Theme
# (.streamlit/config.toml) hinausgehen. EIN einheitliches, kräftiges Blau
# (`--clv-blue`) trägt alle Primär-Buttons und die aktive Navigation -
# keine Farbverläufe mehr auf flächigen UI-Elementen (siehe Designrichtung
# "vereinheitliche das komplette Design" - vorherige Blau-→-Violet-→-Red-
# Verläufe auf Buttons/Kopfboxen wurden bewusst zurückgebaut, weil sie der
# geforderten Konsistenz/Ruhe entgegenstanden). `--clv-gradient` und
# `--clv-gradient-soft` bleiben nur für die (unveränderte) Wortmarke bzw.
# den Startseiten-Claim erhalten, siehe deren jeweilige Regeln unten.
_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

:root {
    --clv-navy: #111827;
    --clv-blue: #2563EB;
    --clv-blue-dark: #1D4ED8;
    --clv-violet: #7C3AED;
    --clv-red: #E53935;
    --clv-bg: #FFFFFF;
    --clv-bg-alt: #F1F5F9;
    --clv-white: #FFFFFF;
    --clv-muted: #64748B;
    --clv-border: rgba(17, 24, 39, 0.09);
    --clv-border-strong: rgba(17, 24, 39, 0.16);
    /* Nur noch für die (unveränderte) Wortmarke bzw. deren Icon-Kachel
       verwendet (siehe .avq-marke-icon/.avq-tagline) - Primär-Buttons und
       Hero-Icons nutzen jetzt das einheitliche `--clv-blue`. */
    --clv-gradient: linear-gradient(135deg, var(--clv-blue) 0%, var(--clv-violet) 55%, var(--clv-red) 100%);
    --clv-gradient-soft: linear-gradient(150deg, #3E63C9 0%, #7C6BC4 45%, #C24B45 100%);
    --clv-glow-blue: rgba(37, 99, 235, 0.20);
    --clv-glow-red: rgba(229, 57, 53, 0.17);
    --clv-radius: 18px;
    --clv-radius-sm: 12px;
    --clv-shadow-sm: 0 1px 2px rgba(17, 24, 39, 0.04), 0 1px 3px rgba(17, 24, 39, 0.06);
    --clv-shadow-md: 0 8px 24px rgba(17, 24, 39, 0.08), 0 2px 6px rgba(17, 24, 39, 0.05);
    --clv-shadow-lg: 0 16px 40px rgba(17, 24, 39, 0.12), 0 4px 10px rgba(17, 24, 39, 0.06);
    --clv-transition: 200ms cubic-bezier(0.4, 0, 0.2, 1);
}

html, body, [class*="css"] {
    font-family: "Inter", "Segoe UI", sans-serif;
}

/* Hauptbereich: reiner, konsistenter weißer Hintergrund (kein Mesh, kein
   Verlauf) - siehe Designrichtung "Gesamthintergrund der Anwendung soll
   komplett weiß sein". Die frühere, mehrschichtige animierte Mesh-/
   Ring-Deko-Ebene wurde bewusst entfernt: Sie stand der geforderten
   Einheitlichkeit/Ruhe entgegen. Die Sidebar bleibt bewusst dunkel mit
   ihrer eigenen, davon unabhängigen dezenten Deko (siehe weiter unten)
   - das ist etablierter, wiederholt bestätigter Kontrast zur weißen
   Anwendungsfläche und nicht Teil dieser Vereinheitlichung. */
[data-testid="stMain"] {
    background-color: var(--clv-bg);
}

/* Dezenter, animierter Blau-/Rot-Glow hinter dem Startseiten-Hero
   (Wortmarke + Claim), siehe hero_glow(). Absolut positioniert relativ
   zu .block-container (siehe position: relative dort weiter unten);
   sehr geringe Deckkraft, rein dekorativ (aria-hidden, pointer-events:
   none), sodass er weder Text verdeckt noch klickbar ist. */
.clv-hero-glow {
    position: absolute;
    top: -1.5rem;
    left: 50%;
    transform: translateX(-50%);
    width: min(54rem, 94vw);
    height: 22rem;
    pointer-events: none;
    z-index: 0;
    background: radial-gradient(closest-side, var(--clv-glow-blue), rgba(124, 58, 237, 0.09) 45%, var(--clv-glow-red) 72%, transparent 82%);
}
@media (prefers-reduced-motion: no-preference) {
    .clv-hero-glow {
        animation: clv-hero-pulse 14s ease-in-out infinite;
    }
}
@keyframes clv-hero-pulse {
    0%, 100% { opacity: 0.75; transform: translateX(-50%) scale(1); }
    50%      { opacity: 1; transform: translateX(-50%) scale(1.04); }
}

/* Streamlits fixe Kopfzeile (Deploy-/Menü-Leiste) ist absolut
   positioniert, 60px hoch und deckend (siehe [data-testid="stHeader"]),
   und liegt über dem Content-Bereich statt Platz für ihn zu reservieren.
   padding-top MUSS diese 60px überschreiten, sonst rendert der Anfang
   des Inhalts (insbesondere die große Wortmarke auf der Startseite)
   unter der Kopfzeile und wird von ihr überdeckt/"abgeschnitten". */
.block-container {
    position: relative;
    max-width: 1000px;
    padding-top: 4.75rem;
    padding-bottom: 3rem;
}
[data-testid="stSidebar"] .block-container {
    padding-top: 2rem;
}

/* Sehr dezentes Raster + zwei ruhige Glows in der dunklen Sidebar -
   bewusst auf `.block-container` selbst (nicht auf den äußeren
   `[data-testid="stSidebar"]`-Wrapper) als eigenes `background-image`
   gesetzt: `.block-container` ist bereits ein reiner Inhalts-Container
   ohne eigene Positionierungs-/Scroll-Logik, während der äußere
   Sidebar-Wrapper (fixe Breite, ggf. eigenes Scroll-/Fixed-Verhalten
   durch Streamlit selbst) NICHT angefasst wird - so bleibt die
   native Sidebar-Mechanik (Scrollen, Ein-/Ausklappen) unangetastet.
   Sehr geringe Deckkraft, damit Navigation/Text jederzeit gut lesbar
   bleiben. */
[data-testid="stSidebar"] .block-container {
    background-repeat: repeat, repeat, no-repeat, no-repeat;
    background-image:
        repeating-linear-gradient(90deg, rgba(255, 255, 255, 0.035) 0, rgba(255, 255, 255, 0.035) 1px, transparent 1px, transparent 42px),
        repeating-linear-gradient(0deg, rgba(255, 255, 255, 0.035) 0, rgba(255, 255, 255, 0.035) 1px, transparent 1px, transparent 42px),
        radial-gradient(20rem 18rem at 100% -8%, rgba(37, 99, 235, 0.16), transparent 65%),
        radial-gradient(18rem 22rem at -10% 108%, rgba(229, 57, 53, 0.1), transparent 65%);
}
@media (prefers-reduced-motion: no-preference) {
    [data-testid="stSidebar"] .block-container {
        animation: clv-sidebar-glow-drift 52s ease-in-out infinite alternate;
    }
}
@keyframes clv-sidebar-glow-drift {
    0%   { background-position: 0 0, 0 0, 0% 0%, 0% 0%; }
    50%  { background-position: 0 0, 0 0, -2% 3%, 2% -3%; }
    100% { background-position: 0 0, 0 0, 3% -2%, -3% 2%; }
}
@media (max-width: 640px) {
    [data-testid="stSidebar"] .block-container {
        animation: none;
    }
}

h1, h2, h3 {
    letter-spacing: -0.01em;
}

/* Clevoriq-Wortmarke (Icon + Schriftzug + optionaler, dezenter
   Produkt-Indikator darunter, z. B. "Documents"), siehe marke_kopf().
   Keine eigene Textfarbe - sie erbt bewusst die Umgebungsfarbe (helle
   Sidebar-Schrift auf Navy vs. dunkle Schrift im hellen Content-Bereich).
   Clevoriq ist die dominante Plattform-/Konto-Marke, der Produkt-Name
   (aktuell nur "Documents") steht bewusst kleiner/zurückhaltender
   darunter statt gleichrangig daneben - siehe CLAUDE.md
   "Platform & Product Branding". position: relative + z-index sorgen
   dafür, dass die Wortmarke immer über dem Hero-Glow (siehe
   .clv-hero-glow) liegt, unabhängig von dessen DOM-Position. */
.avq-marke {
    position: relative;
    z-index: 1;
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
    background: var(--clv-gradient-soft);
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
    letter-spacing: 0.02em;
    font-size: 1.15rem;
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
    width: 3.75rem;
    height: 3.75rem;
    border-radius: 0.95rem;
    font-size: 2rem;
}
.avq-marke--gross .avq-marke-text {
    font-size: 2.3rem;
}
.avq-marke--gross .avq-marke-produkt {
    font-size: 0.95rem;
}

/* Tagline auf der Startseite - einer der wenigen bewussten
   Farbverlauf-Akzente (Highlight), siehe marke_tagline(). Ebenfalls
   über dem Hero-Glow positioniert (siehe .avq-marke oben). */
.avq-tagline {
    position: relative;
    z-index: 1;
    font-size: 1.35rem;
    font-weight: 600;
    line-height: 1.3;
    margin: 0.9rem 0 0.4rem 0;
    background: var(--clv-gradient);
    -webkit-background-clip: text;
    background-clip: text;
    color: transparent;
}

/* Seiten-Hero-Band, siehe seiten_hero() - der einheitliche Kopfbereich
   oben auf jeder größeren Seite (Chat, Analyse & Vergleich, Dokument
   prüfen, Bibliothek, Konto & Sicherheit), gefolgt vom gewohnten hellen
   Inhaltsbereich darunter. Eine einzige, helle Variante für alle Seiten
   (weißer Hintergrund, feine Border, dezenter Schatten) - eine frühere
   zusätzliche dunkle Navy-Variante (`.clv-hero-band--dark`) plus eigene
   animierte Raster-/Glow-Deko-Ebene wurde entfernt, weil das genau die
   Uneinheitlichkeit erzeugte, die die Designrichtung jetzt ausdrücklich
   nicht mehr will: alle Kopfboxen sehen jetzt exakt gleich aus (gleicher
   Hintergrund, gleiche Abstände, Rundungen, Schatten, Typografie,
   Icon-Darstellung), unabhängig davon, welcher Bereich sie rendert. */
.clv-hero-band {
    border-radius: var(--clv-radius);
    padding: 2.25rem 2rem;
    margin-bottom: 1.75rem;
    background: var(--clv-white);
    border: 1px solid var(--clv-border);
    box-shadow: var(--clv-shadow-sm);
}
.clv-hero-row {
    display: flex;
    align-items: flex-start;
    gap: 1rem;
    min-width: 0;
}
.clv-hero-text {
    min-width: 0;
}
.clv-hero-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    width: 3rem;
    height: 3rem;
    border-radius: 0.85rem;
    background: rgba(37, 99, 235, 0.1);
    font-size: 1.4rem;
    line-height: 1;
}
.clv-hero-title {
    margin: 0;
    font-size: 1.85rem;
    font-weight: 800;
    letter-spacing: -0.01em;
    color: var(--clv-navy);
    line-height: 1.2;
}
.clv-hero-subtitle {
    margin: 0.4rem 0 0 0;
    font-size: 0.98rem;
    color: var(--clv-muted);
    line-height: 1.45;
}
@media (max-width: 640px) {
    .clv-hero-band {
        padding: 1.5rem 1.25rem;
    }
    .clv-hero-title {
        font-size: 1.5rem;
    }
}

/* Zurückhaltender, professioneller Hinweis-Baustein (z. B. Disclaimer
   in Analyse & Vergleich / Dokument prüfen) statt einer auffälligen
   Warnbox, siehe hinweis_dezent(). */
.avq-hinweis {
    font-size: 0.85rem;
    color: #52525B;
    background: var(--clv-bg-alt);
    border-left: 3px solid var(--clv-blue);
    border-radius: 8px;
    padding: 0.55rem 0.85rem;
    margin: 0.35rem 0 1rem 0;
}

/* Primär-Buttons (inkl. aktiver Navigation) erhalten ein einheitliches,
   kräftiges Blau (kein Farbverlauf mehr - siehe Designrichtung
   "einheitliches Blau, passend zur Website"). Eine einzige zentrale
   Regel für alle Primär-Buttons (Startseite, Analyse & Vergleich,
   Dokument-prüfen-Kategorien, "Kompletten Dokumenten-Check starten",
   aktive Navigation) statt Einzelstyling je Seite.

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
    background: var(--clv-blue) !important;
    border: none !important;
    color: #FFFFFF !important;
    display: flex;
    align-items: center;
    justify-content: center;
    text-align: center;
    transition: background-color var(--clv-transition), transform var(--clv-transition), box-shadow var(--clv-transition);
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
    background: var(--clv-blue-dark) !important;
    color: #FFFFFF !important;
    transform: translateY(-1px);
    box-shadow: 0 6px 18px rgba(37, 99, 235, 0.24);
}
button[kind="primary"]:active,
[data-testid="stBaseButton-primary"]:active {
    transform: translateY(0);
}

/* Sekundär-Buttons im hellen Hauptbereich: dezente, kartenartige Optik
   (weiß/hellgrau + feine Border) statt der reinen Theme-Vorgabe, mit
   sanftem Hover (leichte Anhebung + Border-Tönung Richtung Blau) - siehe
   "Buttons"-Abschnitt der Designrichtung. Bewusst auf den Hauptbereich
   beschränkt (siehe eigene Sidebar-Regel weiter unten), damit die dunkle
   Sidebar ihre eigene, dort besser lesbare Sekundär-Optik behält. */
[data-testid="stMain"] button[kind="secondary"],
[data-testid="stMain"] [data-testid="stBaseButton-secondary"] {
    background: var(--clv-white) !important;
    color: var(--clv-navy) !important;
    border: 1px solid var(--clv-border-strong) !important;
    transition: transform var(--clv-transition), box-shadow var(--clv-transition), border-color var(--clv-transition), background var(--clv-transition);
}
[data-testid="stMain"] button[kind="secondary"]:hover,
[data-testid="stMain"] [data-testid="stBaseButton-secondary"]:hover {
    border-color: var(--clv-blue) !important;
    background: var(--clv-bg-alt) !important;
    transform: translateY(-1px);
    box-shadow: var(--clv-shadow-sm);
}

/* Sekundär-Buttons in der dunklen Sidebar (u. a. inaktive Navigation,
   "Neuer Chat", Chat-Liste, "Konto & Sicherheit"/"Abmelden"): dezente
   Ghost-Optik statt einer hellen Fläche, damit sie auf Navy gut lesbar
   bleiben - sanfter Hover-Hintergrund statt Farbverlauf/Anhebung. */
[data-testid="stSidebar"] button[kind="secondary"],
[data-testid="stSidebar"] [data-testid="stBaseButton-secondary"] {
    background: rgba(255, 255, 255, 0.04) !important;
    border: 1px solid rgba(255, 255, 255, 0.08) !important;
    transition: background var(--clv-transition), border-color var(--clv-transition);
}
[data-testid="stSidebar"] button[kind="secondary"]:hover,
[data-testid="stSidebar"] [data-testid="stBaseButton-secondary"]:hover {
    background: rgba(255, 255, 255, 0.09) !important;
    border-color: rgba(37, 99, 235, 0.5) !important;
}

/* Aktive Navigation (Primär-Button in der Sidebar) - dasselbe
   einheitliche Blau wie andere Primär-Buttons, aber ohne die
   Hover-Anhebung (Navigation soll ruhig wirken, nicht "wegspringen"). */
[data-testid="stSidebar"] button[kind="primary"]:hover,
[data-testid="stSidebar"] [data-testid="stBaseButton-primary"]:hover {
    transform: none;
    box-shadow: 0 4px 14px rgba(37, 99, 235, 0.28);
}

/* Dezente, moderne Ladezustands-Optik (st.spinner) - keine hektische
   native Optik, sondern ein sanfter Blau-Glow um das ohnehin native
   Spinner-Icon. Rein visuell, keine funktionale Änderung. */
[data-testid="stSpinner"] svg {
    color: var(--clv-blue);
    filter: drop-shadow(0 0 5px rgba(37, 99, 235, 0.35));
}

/* Moderne Formulareingaben (Textfelder, Auswahlfelder, Datum, Zahl,
   Chateingabe) - dezente Border statt der nativen Theme-Vorgabe, klarer
   Blau-Fokuszustand mit weichem Glow, einheitlicher Radius. Bewusst NUR
   auf den Hauptbereich beschränkt (Formulare in der Sidebar - aktuell
   keine - blieben sonst auf dunklem Grund unlesbar hell/weiß). */
[data-testid="stMain"] [data-testid="stTextInput"] input,
[data-testid="stMain"] [data-testid="stTextArea"] textarea,
[data-testid="stMain"] [data-testid="stDateInput"] input,
[data-testid="stMain"] [data-testid="stNumberInput"] input,
[data-testid="stMain"] [data-baseweb="select"] > div {
    border-radius: var(--clv-radius-sm) !important;
    border: 1px solid var(--clv-border-strong) !important;
    background: var(--clv-white) !important;
    transition: border-color var(--clv-transition), box-shadow var(--clv-transition);
}
[data-testid="stMain"] [data-testid="stTextInput"] input:focus,
[data-testid="stMain"] [data-testid="stTextArea"] textarea:focus,
[data-testid="stMain"] [data-testid="stDateInput"] input:focus,
[data-testid="stMain"] [data-testid="stNumberInput"] input:focus,
[data-testid="stMain"] [data-baseweb="select"]:focus-within > div {
    border-color: var(--clv-blue) !important;
    box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.14) !important;
}
[data-testid="stMain"] [data-testid="stChatInput"] {
    border-radius: var(--clv-radius) !important;
    border: 1px solid var(--clv-border-strong) !important;
    background: var(--clv-white) !important;
    transition: border-color var(--clv-transition), box-shadow var(--clv-transition);
}
[data-testid="stMain"] [data-testid="stChatInput"]:focus-within {
    border-color: var(--clv-blue) !important;
    box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.14) !important;
}

/* Chat-Bubbles: eigene, hochwertigere Kartenoptik statt der schlichten
   Theme-Vorgabe - Nutzerfragen dezent blau getönt, KI-Antworten neutral
   weiß, damit die Rollen auf einen Blick erkennbar bleiben, ohne bunt zu
   wirken. `:has()` selektiert über die Avatar-Kindelemente, die
   Streamlit je Rolle unterschiedlich rendert (siehe
   `stChatMessageAvatarUser`/`stChatMessageAvatarAssistant`). */
[data-testid="stChatMessage"] {
    border-radius: var(--clv-radius) !important;
    border: 1px solid var(--clv-border);
    box-shadow: var(--clv-shadow-sm);
    margin-bottom: 0.6rem;
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
    background: rgba(37, 99, 235, 0.04);
    border-color: rgba(37, 99, 235, 0.14);
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) {
    background: var(--clv-white);
}

/* Quellenangabe (siehe quellen_hinweis()) als kleiner, hochwertiger
   "Chip" statt einer reinen Fließtext-Caption. */
[data-testid="stMain"] [data-testid="stChatMessage"] [data-testid="stCaptionContainer"] {
    display: inline-block;
    margin-top: 0.35rem;
    padding: 0.2rem 0.65rem;
    border-radius: 999px;
    background: var(--clv-bg-alt);
    border: 1px solid var(--clv-border);
}

/* Einheitliches, modernes Kartensystem für alle umrandeten Container
   (Startseite, Dokumentenbibliothek, Analyse & Vergleich, Dokument
   prüfen, Konto & Sicherheit, Login/Register) - weißer Hintergrund
   (bereits über das native Theme), größerer Radius, dezenter Schatten
   und ein sehr kurzes, ruhiges Erscheinen/Hover statt einer stark
   springenden Animation. Bewusst NUR auf den Hauptbereich beschränkt. */
[data-testid="stMain"] [data-testid="stVerticalBlockBorderWrapper"] {
    border-radius: var(--clv-radius) !important;
    box-shadow: var(--clv-shadow-sm);
    transition: transform var(--clv-transition), box-shadow var(--clv-transition), border-color var(--clv-transition);
}
[data-testid="stMain"] [data-testid="stVerticalBlockBorderWrapper"]:hover {
    transform: translateY(-2px);
    box-shadow: var(--clv-shadow-md);
    border-color: var(--clv-border-strong) !important;
}
@media (prefers-reduced-motion: no-preference) {
    [data-testid="stMain"] [data-testid="stVerticalBlockBorderWrapper"] {
        animation: clv-fade-in 240ms ease-out;
    }
}
@keyframes clv-fade-in {
    from { opacity: 0; transform: translateY(6px); }
    to   { opacity: 1; transform: translateY(0); }
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
/* Dezenter, alternierender Blau-/Rot-Glow beim Hover der vier großen
   Startseiten-Karten (siehe Designrichtung "Border/Glow darf leicht
   blau oder rot reagieren") - ergänzt die generelle Karten-Hover-Regel
   oben um eine kartenspezifische Akzentfarbe statt sie zu ersetzen. */
[class*="st-key-home_karte_"]:hover {
    border-color: rgba(37, 99, 235, 0.35) !important;
    box-shadow: var(--clv-shadow-md), 0 0 0 1px rgba(37, 99, 235, 0.06);
}
[class*="st-key-home_karte_analyse"]:hover,
[class*="st-key-home_karte_pruefung"]:hover {
    border-color: rgba(229, 57, 53, 0.32) !important;
    box-shadow: var(--clv-shadow-md), 0 0 0 1px rgba(229, 57, 53, 0.06);
}

/* Kompakte Aktions-Karten (Dokument prüfen / Analyse & Vergleich), siehe
   modus_karte(). Kind-Reihenfolge (per :nth-child, daher genau in dieser
   Reihenfolge): (1) farbige Icon-Kachel, (2) Titel, (3) Beschreibung,
   (4) Button, (5) Hinweiszeile. Titel/Beschreibung reservieren eine
   nachgemessene Mindesthöhe für den jeweils längsten real vorkommenden
   Umbruch (Titel zweizeilig, Beschreibung bis zu dreizeilig bei der
   schmaleren Analyse-Kartenbreite) - dadurch werden alle Karten einer
   Gruppe exakt gleich hoch statt nur ungefähr. Die fünfte Zeile
   (Hinweistext bei deaktiviertem Button) wird von modus_karte() IMMER
   gerendert (als unsichtbarer Platzhalter, wenn kein Hinweis nötig ist),
   damit Karten mit und ohne Hinweistext nicht unterschiedlich hoch
   werden, z. B. wenn in Analyse & Vergleich je nach Dokumentauswahl nur
   ein Teil der Karten deaktiviert ist. margin-top:auto auf dem Button
   schiebt ihn in jeder Karte an denselben unteren Rand. */
[class*="st-key-modus_karte_"] > [data-testid="stElementContainer"]:nth-child(2) {
    min-height: 3.25rem;
}
[class*="st-key-modus_karte_"] > [data-testid="stElementContainer"]:nth-child(3) {
    min-height: 4.3rem;
}
[class*="st-key-modus_karte_"] > [data-testid="stElementContainer"]:has(.stButton) {
    margin-top: auto;
    padding-top: 0.5rem;
}
[class*="st-key-modus_karte_"] > [data-testid="stElementContainer"]:nth-child(5) {
    min-height: 2.8rem;
}

/* Kleine, farbige Icon-Kachel für Karten (aktuell modus_karte()) -
   alternierend Blau/Violett/Rot statt eines einzelnen Markenverlaufs je
   Karte (siehe Designrichtung "Farbliche Icons/Akzente alternierend").
   Reiner Tönungshintergrund (12 % Deckkraft der jeweiligen Markenfarbe),
   kein Farbverlauf - das Emoji-Icon selbst trägt bereits seine eigene
   Farbe. */
.clv-icon-chip {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 2.5rem;
    height: 2.5rem;
    border-radius: 0.7rem;
    font-size: 1.15rem;
    line-height: 1;
    margin-bottom: 0.5rem;
}
.clv-icon-chip--blau {
    background: rgba(37, 99, 235, 0.12);
}
.clv-icon-chip--violett {
    background: rgba(124, 58, 237, 0.12);
}
.clv-icon-chip--rot {
    background: rgba(229, 57, 53, 0.12);
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


# Clevoriq ist die zentrale Plattform-/Konto-Marke; das aktuelle Produkt
# ("Documents") ist eines von künftig mehreren Clevoriq-Produkten auf
# demselben Clevoriq-Konto (siehe CLAUDE.md "Platform & Product
# Branding") - als Modul-Konstante statt eines in marke_kopf()
# hartkodierten Strings, damit ein künftiges zweites Produkt (z. B.
# "Invoice"/"Vault") seinen eigenen Produkt-Namen einfach über den
# `produkt`-Parameter reinreicht, ohne diese Datei anzufassen. Es wird
# hier bewusst NUR die Konstante für das existierende Produkt vorbereitet
# - kein Produkt-Switcher, keine Platzhalter für weitere Produkte.
PRODUKT_NAME = "Documents"


def marke_kopf(gross=False, produkt=PRODUKT_NAME):
    """Rendert die Clevoriq-Wortmarke (Farbverlauf-Icon + Schriftzug),
    mit einem optionalen, dezenten Produkt-Indikator (z. B. "Documents")
    kleiner darunter - Clevoriq bleibt visuell die dominante Marke, das
    Produkt steht sichtbar, aber deutlich zurückhaltender (siehe
    `.avq-marke-produkt` in `_CSS`). `produkt=None` blendet die Zeile
    aus (z. B. für einen künftigen Kontext ohne Produktbezug); der
    Parameter existiert bewusst, damit ein späteres zweites Clevoriq-
    Produkt dieselbe Funktion mit einem eigenen Namen aufrufen kann,
    statt eine eigene Wortmarken-Komponente zu bauen.

    Trägt `translate="no"` + die Klasse "notranslate" (von Chrome/Google
    Translate ausgewertet), damit ein aktives Seiten-Übersetzungs-Tool im
    Browser die Markennamen "Clevoriq"/"Documents" nicht als normale
    englische/erkennbare Wörter fehlübersetzt (z. B. "Documents" ->
    "Unterlagen") - reine Markenschutz-Maßnahme, kein Sprachwechsel der
    übrigen (bewusst deutschsprachigen) Oberfläche.

    Das Icon ist bewusst ein reines CSS-/Text-Icon (Farbverlauf-Kachel +
    "C", siehe `.avq-marke-icon` in `_CSS`) statt des inline eingebetteten
    `assets/logo_icon.svg`: `st.html` sanitisiert seinen Inhalt mit
    DOMPurify, das SVG-Elemente entfernt, wodurch das Icon unsichtbar
    bliebe. Die Wortmarke erbt ihre Textfarbe bewusst von der Umgebung
    (helle Schrift in der dunklen Sidebar, dunkle Schrift im hellen
    Content-Bereich). `assets/logo_icon.svg`/`assets/logo.svg` bleiben als
    vorbereitete Quell-Assets bestehen (z. B. für ein späteres Favicon
    oder eine gerasterte Grafik) - ein endgültiges Logo-Asset kann diese
    Funktion später ersetzen, ohne dass sich ihre Aufrufstellen ändern
    müssen. CSS-Klassennamen (Präfix "avq-") bleiben technisch
    unverändert (reines Implementierungsdetail ohne sichtbaren
    Markenbezug), um das Diff beim Rebranding klein zu halten.
    """
    klasse = "avq-marke avq-marke--gross" if gross else "avq-marke"
    produkt_html = f'<span class="avq-marke-produkt">{produkt}</span>' if produkt else ""
    st.html(
        f'<div class="{klasse} notranslate" translate="no">'
        '<span class="avq-marke-icon">C</span>'
        '<span class="avq-marke-wortmarke">'
        '<span class="avq-marke-text">Clevoriq</span>'
        f"{produkt_html}"
        "</span>"
        "</div>"
    )


def marke_tagline():
    """Rendert den Clevoriq-Claim als Farbverlauf-Highlight (Startseiten-Hero)."""
    st.html(
        '<p class="avq-tagline">Dokumente verstehen.<br>'
        "Entscheidungen vereinfachen.</p>"
    )


def hero_glow():
    """Rendert einen sehr dezenten, animierten Blau-/Rot-Glow (siehe
    `.clv-hero-glow` in `_CSS`) hinter dem Startseiten-Hero.

    Rein dekorativ (aria-hidden, pointer-events: none in der CSS-Regel) -
    beeinflusst weder Layout noch Funktion, nur die Optik direkt hinter
    `marke_kopf(gross=True)` + `marke_tagline()` auf der Startseite.
    """
    st.html('<div class="clv-hero-glow" aria-hidden="true"></div>')


def seiten_hero(icon, titel, untertitel=None):
    """Rendert das Hero-Band am Kopf einer größeren Seite (siehe
    `.clv-hero-band` in `_CSS`) - der EINE einheitliche Kopfbereich
    (Icon-Kachel, große Headline, Untertitel auf weißem Grund), gefolgt
    vom gewohnten hellen Inhaltsbereich darunter. Wird von Chat, Analyse
    & Vergleich, Dokument prüfen, Bibliothek und Konto & Sicherheit
    gleichermaßen genutzt, damit alle Bereichs-Kopfboxen exakt gleich
    aussehen (siehe Designrichtung "vereinheitliche das komplette
    Design"). Es gibt bewusst keine zweite (z. B. dunkle) Variante mehr -
    genau das erzeugte zuvor die unerwünschte Uneinheitlichkeit.

    `titel`/`untertitel` werden IMMER über `html.escape` eingebettet,
    obwohl die meisten Aufrufer nur feste, im Code definierte Strings
    übergeben - eine Ausnahme ist der Chat-Bereich, dessen Titel aus der
    ersten Nutzerfrage abgeleitet wird (`speicher._kurztitel_erzeugen`)
    und damit Nutzereingabe ist. Da `st.html()` anders als `st.title()`/
    `st.caption()` NICHT automatisch escaped, wäre ein ungeschützter
    Aufruf hier eine echte HTML-Injection-Lücke - deshalb pauschal für
    jeden Aufrufer escapen statt sich auf "ist doch nur ein fester
    String" zu verlassen.
    """
    icon_span = f'<span class="clv-hero-icon">{html.escape(icon)}</span>' if icon else ""
    untertitel_p = (
        f'<p class="clv-hero-subtitle">{html.escape(untertitel)}</p>' if untertitel else ""
    )
    st.html(
        '<div class="clv-hero-band">'
        '<div class="clv-hero-row">'
        f"{icon_span}"
        '<div class="clv-hero-text">'
        f'<h2 class="clv-hero-title">{html.escape(titel)}</h2>'
        f"{untertitel_p}"
        "</div>"
        "</div>"
        "</div>"
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


def modus_karte(icon, titel, beschreibung, button_label, key, deaktiviert=False, deaktiviert_hinweis=None, button_typ="secondary", akzent="blau"):
    """Kompakte Aktions-Karte (z. B. eine Analyse-/Prüfkategorie).

    Trägt einen `st-key-modus_karte_*`-Hook (siehe `_CSS`), damit Titel
    und Beschreibung unabhängig von ihrer Zeilenzahl gleich viel Platz
    reservieren und der Button in jeder Karte einer Gruppe auf gleicher
    Höhe am unteren Rand sitzt - unabhängig davon, ob Titel/Beschreibung
    ein- oder zweizeilig umbrechen. Wird sowohl von Dokument prüfen als
    auch von Analyse & Vergleich genutzt, damit beide Kartengruppen
    automatisch konsistent bleiben.

    `akzent` ("blau"/"violett"/"rot") färbt die kleine Icon-Kachel über
    dem Titel (siehe `.clv-icon-chip` in `_CSS`) - Aufrufer alternieren
    das üblicherweise pro Karte (siehe Designrichtung "Farbliche
    Icons/Akzente alternierend"). Sie ist bewusst das ERSTE Kind im
    Container: die `:nth-child`-Regeln in `_CSS`, die Titel/Beschreibung/
    Hinweiszeile ausrichten, zählen ab dieser Kachel - wer hier ein
    weiteres Element vor Titel/Beschreibung ergänzt, muss auch die
    `:nth-child`-Indizes dort anpassen.

    `button_typ` steuert nur die Button-Optik (siehe Streamlits eigenes
    `type=`) - Standard bleibt "secondary" (Dokument prüfen, unverändert),
    Analyse & Vergleich übergibt "primary" für den Clevoriq-Farbverlauf.
    Die zentrale `button[kind="primary"]`-Regel in `_CSS` sorgt dafür,
    dass ein deaktivierter Primär-Button trotzdem klar deaktiviert
    aussieht statt fälschlich aktiv zu wirken.

    Gibt True zurück, wenn der Button in diesem Lauf geklickt wurde.
    """
    with st.container(border=True, key=f"modus_karte_{key}"):
        st.html(f'<span class="clv-icon-chip clv-icon-chip--{akzent}">{html.escape(icon)}</span>')
        st.markdown(f"**{titel}**")
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
