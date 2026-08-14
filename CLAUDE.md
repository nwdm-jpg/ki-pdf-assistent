# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

**AVENLOQ** ("Dokumente verstehen. Entscheidungen vereinfachen.") is a German-language, AI-powered multi-format document platform — not a "PDF assistant"; PDF is one of several supported formats and should only be named when the PDF format specifically is meant. The primary app (`web_app.py`) is a Streamlit workspace: users build a persistent, multi-format **document library**, then use it across three AI features — a multi-chat **Chat** assistant, one-off **Analyse & Vergleich** (summarize/compare/deadlines/risks), and a category-based **Dokument prüfen** (document check). Retrieval is semantic (OpenAI embeddings + cosine similarity, with a small keyword bonus), not plain keyword overlap. A separate, much simpler CLI (`app.py`) still exists for a single PDF and has not been migrated to any of this — it keeps its original keyword-overlap retrieval, single-question, no-history behavior, and PDF-only scope by design. The project's repo/folder name (`ki-pdf-assistent`) predates the AVENLOQ rebrand and is not itself user-facing. There is no backend framework, no external database, and no automated test suite — this is a small, personal project.

Supported document formats: **PDF, DOCX, TXT, MD, CSV, XLSX, PPTX** (see `dokument_verarbeitung.SUPPORTED_EXTENSIONS`).

## Commands

`requirements.txt` lists runtime dependencies (`streamlit`, `openai`, `pypdf`, `numpy`, `python-docx`, `openpyxl`, `python-pptx`); there is no `pyproject.toml`. Install into the existing `.venv`.

```bash
# Activate the existing venv (Windows)
.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the Streamlit web app (primary entry point: library, chats, analysis, document check)
streamlit run web_app.py

# Run the legacy CLI version (single PDF from pdfs/, keyword retrieval, no history)
python app.py

# Sanity-check the OpenAI API connection/credentials
python api_test.py
```

An `OPENAI_API_KEY` must be available in the environment (the `openai.OpenAI()` client picks it up automatically) for any of the above to work.

There are no lint, format, or test commands configured in this repo.

## Architecture

### Module/file structure

- **`web_app.py`** — Streamlit UI, the main entry point. Five areas selected via `st.session_state.aktiver_bereich` (a plain session variable, not a widget-bound key, so both the sidebar nav and the start-page cards can set it): Startseite (home), 💬 Chat, 🔍 Analyse & Vergleich, 🛡️ Dokument prüfen, 📚 Dokumentenbibliothek. Owns the single upload/processing path (`dateien_verarbeiten`) and all Streamlit widget/spinner/error-boundary orchestration.
- **`app.py`** — legacy CLI for a *single* PDF: loads a file by filename from `pdfs/`, loops on stdin questions until `ende`. Independent, unmigrated code path — no multi-document, no library, no semantic search, no chat history.
- **`pdf_logik.py`** — owns the module-level OpenAI `client` and `MODELL` (`"gpt-5-mini"`, `client.responses.create`, Responses API) that every other module imports rather than creating its own client. Otherwise holds only the legacy, PDF-only, keyword-overlap pipeline used exclusively by `app.py` (`pdf_seiten_extrahieren`, `relevante_seiten_ermitteln`, `relevanten_text_zusammenstellen`, `verwendete_quellen`, `formatiere_quellenhinweis`, `frage_beantworten`). `web_app.py` imports only `frage_beantworten` from here for the Chat area's final answer call; everything else in the multi-document path uses `quellen.py` / `retrieval.py` / `ki_analyse.py` instead of this module's equivalents.
- **`dokument_verarbeitung.py`** — format-detecting document parser. Supports PDF, DOCX, TXT, MD, CSV, XLSX, PPTX (`SUPPORTED_EXTENSIONS`, `PARSER_JE_ENDUNG`). Each format has a small `_..._einheiten` parser that splits a file into "Einheiten" (page/slide/sheet/section — one logical unit each); all parsers feed into the shared `text_in_chunks_aufteilen` (sentence-aware, ~1000 chars, 150-char overlap). Adding a new format means writing one parser function and a `PARSER_JE_ENDUNG` entry — the rest of the pipeline (chunking, storage, retrieval, source formatting) is unchanged. Unsupported extensions raise `NichtUnterstuetzterDateityp` with a clear German message instead of crashing.
- **`retrieval.py`** — semantic search over stored chunks using OpenAI embeddings (`text-embedding-3-small`). Score = cosine similarity (primary) + a small (0.15) keyword-overlap bonus, so exact term matches aren't lost to pure embedding similarity. Selects the top N chunks **per document**, never a single global top-N — this guarantees every active document stays visible to the model even when chat-history context (`zusatzkontext`) biases the query toward one document. `embeddings_batch_erstellen` embeds a whole document's chunks in one API call.
- **`speicher.py`** — SQLite + local file persistence, all under project-local `app_daten/` (gitignored). See "Persistent storage" below.
- **`quellen.py`** — the one source-formatting/prompt-text-assembly implementation for the multi-format path (Chat, Analyse & Vergleich, Dokument prüfen): `relevanten_text_zusammenstellen`, `verwendete_quellen`, `formatiere_quellenhinweis`. Format-aware (Seite/Folie/Tabellenblatt/Abschnitt via `EINHEIT_WOERTER`) and backward-compatible with legacy 2-tuple `(dateiname, seitennummer)` sources stored in old chat messages, so no data migration was needed when this replaced the equivalent functions in `pdf_logik.py` for everything except `app.py`.
- **`ki_analyse.py`** — shared retrieval/prompt/API plumbing used by both `analyse.py` and `pruefung.py`, so that logic isn't duplicated between the two features: `ausschnitte_ermitteln` (load a document selection's chunks, semantic-select the best), `ki_anfrage` (assemble system/history/content messages, call the Responses API, return `{text, quellen, quellenhinweis}`), `rueckfrage_beantworten` (generic follow-up-question handler for an already-produced result, parameterized by `kontext_label` so callers keep independent follow-up threads). Also defines the shared `KUERZE_HINWEIS` (terseness instruction) and `QUELLENFORMAT_HINWEIS` (in-text citation format instruction) used by every AI-analysis system prompt.
- **`analyse.py`** — Analyse & Vergleich feature: `zusammenfassen`, `vergleichen`, `fristen_ermitteln`, `risiken_ermitteln`. Each is a fixed topical search query (there's no user question to search on) + a dedicated system prompt, built on `ki_analyse.py`. `rueckfrage_beantworten` wraps `ki_analyse.rueckfrage_beantworten` with `kontext_label="Analyseergebnis"`.
- **`pruefung.py`** — Dokument prüfen feature: data-driven `KATEGORIEN` (risiken, kosten, fristen, pflichten_eigene, pflichten_gegenseite, unklare_regelungen — each icon/title/focus/search-query) and `PRESETS` (allgemein/vertrag/angebot/rechnung — a title + extra focus clause). Adding a category or preset is a new dict entry only. `einzelpruefung` runs one category; `kompletter_check` runs all categories through **one** combined search query + **one** model call (see cost-awareness below). Priority markers 🔴/🟡/🟢 are enforced via `_PRIORITAETS_ANWEISUNG`. `rueckfrage_beantworten` wraps `ki_analyse.rueckfrage_beantworten` with `kontext_label="Prüfungsergebnis"`.
- **`dokumentbibliothek.py`** — pure, Streamlit-independent filter/sort/display helpers for the library view (`dokumente_filtern`, `dokumente_sortieren`, `dateityp_anzeige`, `einheiten_text`, `groesse_text`, date-range constants). Never mutates the documents passed in or any selection/session state — search/sort/filter are purely presentational.
- **`komponenten.py`** — shared Streamlit UI building blocks reused across areas: `nav_eintrag`/`start_karte`/`modus_karte` (navigation and action cards), `dokument_mehrfachauswahl` (multi-select with session-state persistence that survives a run where the widget isn't drawn), `ergebnis_kopf` + `rueckfragen_chat` (result header and generic follow-up chat UI shared by Analyse & Vergleich and Dokument prüfen), `quellen_hinweis`, `seiten_kopf`, `leerer_zustand`, `marke_kopf`/`marke_tagline` (AVENLOQ wordmark/tagline), `hinweis_dezent` (subdued disclaimer notice). Base colors/radii come from the native Streamlit theme (`.streamlit/config.toml`); `css_einbinden` layers the one centralized brand CSS block (gradient, wordmark, Inter font, button-hierarchy/danger styling, structural tweaks like card min-height) on top — see "Brand & Design System" above.
- **`api_test.py`** — minimal standalone script to verify the OpenAI API key/connection works.

### Data model

Two related but distinct entry shapes exist side by side:

- **Legacy Seiteneintrag** (`pdf_logik.py`, used only by `app.py`): `{"dateiname": str, "seitennummer": int, "text": str}` — one page of one PDF.
- **Chunk** (`dokument_verarbeitung.py` onward, used everywhere else): `{"dateiname": str, "seitennummer": int, "einheit_typ": str, "einheit_anzeige": str, "text": str}`, plus `"embedding"` once loaded from/written to storage. `einheit_typ` is one of `seite`/`folie`/`tabellenblatt`/`abschnitt` (see `quellen.EINHEIT_WOERTER`); `einheit_anzeige` is the human-facing label (e.g. a sheet name). `quellen.py` also transparently accepts legacy 2-tuple `(dateiname, seitennummer)` sources, so old stored chat messages keep rendering correctly.

### Persistent storage

Everything lives under project-local `app_daten/` (gitignored — never committed):

- `app_daten/bibliothek.db` — SQLite, four tables: `dokumente` (id, dateiname, hash UNIQUE, seitenzahl, hochgeladen_am, dateityp, einheit_typ, groesse_bytes), `chunks` (id, dokument_id FK CASCADE, seitennummer, text, embedding BLOB, einheit_typ, einheit_anzeige), `chats` (id, titel, erstellt_am, aktualisiert_am, dokument_ids JSON list), `nachrichten` (id, chat_id FK CASCADE, frage, antwort, quellen JSON, erstellt_am).
- `app_daten/pdfs/` — original uploaded file bytes, named `<sha256-hash>.<extension>` (the folder name is historical from the PDF-only era but now holds every supported format). The hash also drives upload dedup (`speicher.dokument_nach_hash`) — re-uploading identical bytes is a no-op.
- Schema changes are additive-only: `speicher._spalten_ergaenzen` checks `PRAGMA table_info` and runs `ALTER TABLE ... ADD COLUMN` for anything missing, on every startup. There is no migration framework and no destructive schema change should be introduced without discussing it first — existing rows/databases must keep working.
- Deleting a document (`speicher.dokument_loeschen`) cascades chunk deletion via FK and also actively strips that document's id from every chat's `dokument_ids`, plus deletes its file copy.
- The project-root `pdfs/` folder is unrelated to any of this — it's `app.py`'s original CLI-only sample folder.

### Multi-format document support

`dokument_verarbeitung.py` supports PDF, DOCX, TXT, MD, CSV, XLSX, PPTX. Each format parses into "Einheiten" — pages (PDF), slides (PPTX), sheets (XLSX, capped at 500 rows/sheet), or size-targeted paragraph sections (~1500 chars — DOCX/TXT/MD via `_absaetze_zu_abschnitten`, CSV in 50-row blocks) — which then all go through the same `text_in_chunks_aufteilen`. This is the only chunking implementation in the app.

### Document library workspace

📚 Dokumentenbibliothek is the single place documents are uploaded and processed (`web_app.py`'s `dateien_verarbeiten`); Chat, Analyse & Vergleich, and Dokument prüfen all read from this shared library rather than re-uploading. The library view adds search, file-type filter, upload-date filter, and eight sort orders, all via the pure functions in `dokumentbibliothek.py` — filtering/sorting never touches selection state in any of the three feature areas.

### Semantic retrieval

`retrieval.py` (embeddings + cosine similarity + keyword bonus, per-document top-N) is used everywhere in the multi-document path — Chat, Analyse & Vergleich (via `ki_analyse.ausschnitte_ermitteln`), Dokument prüfen (same). It supersedes, but does not remove, the plain keyword-overlap retrieval in `pdf_logik.py`, which remains exclusively `app.py`'s (unmigrated) retrieval method.

### Generic source formatting

`quellen.py` is format-aware: it renders `"Seite 3"`, `"Folie 7"`, `"Tabellenblatt \"Kosten\""`, `"Abschnitt 2"` etc. depending on `einheit_typ`, groups by filename (and by unit-type within a filename if a document mixes types), sorts numeric display values ascending and non-numeric ones alphabetically-quoted, and dedups. It replaces `pdf_logik.formatiere_quellenhinweis`/`verwendete_quellen`/`relevanten_text_zusammenstellen` for every caller except `app.py`.

### Multiple chats

`speicher.py`'s `chats`/`nachrichten` tables back a full multi-chat sidebar: create (`chat_erstellen`), list (`chat_liste`, most-recently-updated first), switch, delete (`chat_loeschen`, with fallback to a fresh chat if the active one is deleted). A chat's title is auto-derived from its first question via a plain string heuristic (`_kurztitel_erzeugen`, ≤45 chars) — deliberately *not* a model call, to avoid spending API cost on titling.

### Per-chat document selections

`chats.dokument_ids` (JSON) holds each chat's own active-document set, independent of the Analyse & Vergleich and Dokument prüfen selections. The sidebar's multiselect (visible only while the Chat area is active) uses a per-chat session-state key (`chat_dokument_ids_{chat_id}`) via `komponenten.dokument_mehrfachauswahl`, so switching chats mid-session never bleeds one chat's selection into another's. Only a chat's active documents feed its retrieval and answers; deleted documents are silently dropped from `dokument_ids` on load (`speicher._existierende_dokument_ids`).

### Analyse & Vergleich (`analyse.py`, `web_app.py` BEREICH_ANALYSE)

Four fixed actions over an independently-selected multi-document set (own `session_key`, unrelated to Chat's or Dokument prüfen's): Zusammenfassen (≥1 doc), Dokumente vergleichen (≥2 docs), Fristen & Termine, Risiken & Auffälligkeiten. Each runs a fixed topical search query (no user question exists to search on) through `ki_analyse`, then renders the structured result plus a dedicated follow-up (`rueckfragen`) chat thread held in `st.session_state.analyse_ergebnis`.

### Dokument prüfen (`pruefung.py`, `web_app.py` BEREICH_PRUEFUNG)

Six categories × four presets, both fully data-driven (`KATEGORIEN`, `PRESETS`) so new ones need no UI or control-flow changes. A user can run one category (`einzelpruefung`) or the full check (`kompletter_check` — all categories, one combined query, one model call). Each result gets a priority-annotated (🔴/🟡/🟢) breakdown and its own follow-up chat thread, independent of Analyse & Vergleich's and Chat's, and its own document selection.

### Shared analysis/retrieval helpers (`ki_analyse.py`)

The retrieval → prompt-assembly → Responses-API-call → source-formatting pipeline used by both `analyse.py` and `pruefung.py` lives here exactly once, along with the two shared system-prompt fragments (`KUERZE_HINWEIS`, `QUELLENFORMAT_HINWEIS`) that keep every AI-analysis feature's output equally terse and equally well-sourced. New AI-analysis features (beyond Analyse & Vergleich / Dokument prüfen) should be built on this module rather than re-implementing retrieval or prompt assembly.

### Language convention

All UI strings, variable/function names, and prompts are in German (e.g. `frage` = question, `antwort` = answer, `seiten` = pages, `dokumente` = documents, `bewertete_seiten` = scored pages). Keep new code consistent with this convention.

## Brand & Design System

AVENLOQ's identity is: modern, professional, trustworthy, intelligent, clean, premium SaaS — not playful, not futuristic. Preserve these when touching UI:

- **Palette**: Dark Navy `#0D1026` (sidebar), Primary Blue `#2563EB`, Primary Violet `#7C3AED`, Accent Purple `#A855F7`, Light Lavender `#EDE9FE`, white/near-white content surfaces. Base theme (light content + dark navy sidebar) lives in `.streamlit/config.toml` (`[theme]` + `[theme.sidebar]`); brand tokens, the blue→violet gradient (`--avq-gradient`), and everything the native theme can't express live in the single centralized CSS block in `komponenten.py` (`_CSS`) — don't scatter new CSS/`st.html` styling into `web_app.py`.
- **Gradient discipline**: the blue→violet gradient is reserved for primary actions, active/selected navigation, the wordmark, and the Startseite tagline — driven by `button[kind="primary"]` CSS plus `komponenten.marke_kopf`/`marke_tagline`. Ordinary UI (secondary buttons, cards, inputs) stays neutral/restrained; don't add gradients elsewhere.
- **Button hierarchy**: primary (`type="primary"`) = gradient, for the one main action per view; secondary (`type="secondary"`, the default) = neutral/outlined; danger = the destructive "Endgültig löschen" pattern only, styled red via the `[class*="st-key-bibliothek_confirm_del_"]` CSS hook in `komponenten.py` — follow the same key-prefix pattern for any new destructive-confirmation button rather than reusing `type="primary"`.
- **Wordmark**: `komponenten.marke_kopf(gross=False)` renders the icon+"AVENLOQ" lockup; `gross=True` is the larger Startseite-hero variant. The icon is a pure CSS/text mark (`.avq-marke-icon` gradient tile + "A") rather than an inlined SVG — `st.html` runs its content through DOMPurify, which strips `<svg>` content, so an inline SVG icon renders invisible. `assets/logo.svg` / `assets/logo_icon.svg` remain as prepared, self-authored source assets for a future real logo (e.g. a rasterized favicon or an `st.image`-based swap); update `marke_kopf` to use them once a non-SVG asset (or a sanitizer-safe embedding method) is available, without changing call sites. Never use an emoji as the product logo (emojis remain fine for nav/card icons, e.g. 💬🔍🛡️📚).
- **Typography**: Inter (loaded via Google Fonts `@import` in `komponenten.py`'s CSS, with a system sans-serif fallback), applied globally; avoid decorative fonts or oversized text outside the Startseite hero.
- **Disclaimers**: legal/analysis disclaimers (`analyse.RISIKEN_HINWEIS`, `pruefung.PRUEFUNG_HINWEIS`) render via `komponenten.hinweis_dezent`, a subdued lavender/violet-accent notice — not `st.warning` — to stay professional rather than alarming. Never add "100% secure" / "DSGVO-konform" / "rechtssicher" style claims; auth, hosting, and the final privacy/security architecture don't exist yet.
- **Wording**: refer to the product as "AVENLOQ", never "KI-PDF-Assistent"/"PDF-Assistent". Prefer format-neutral wording ("Dokument", "Datei") in product-facing UI text; keep "PDF" only where the PDF format specifically is meant (`app.py`, `dokument_verarbeitung.py`, `speicher.py`'s `PDF_ORDNER`, format badges, etc. — these are correct as-is, not leftover branding).

## Files

- `pdfs/` — local folder used only by the legacy `app.py` CLI as the source for PDFs by filename; contains sample test PDFs. Not related to the document library. (One sample file's embedded PDF metadata still reads "KI PDF Assistent" from before the rebrand — it's inert binary metadata in a test fixture, not rendered anywhere in the app.)
- `app_daten/` — gitignored runtime data: the SQLite library/chat database and original uploaded files. Created on first run (`speicher.datenbank_initialisieren`); never commit its contents.
- `assets/` — self-authored brand SVGs (`logo.svg`, `logo_icon.svg`) used by `komponenten.marke_kopf`. See "Brand & Design System" above.
- `.streamlit/config.toml` — base AVENLOQ theme (light content area + dark navy `[theme.sidebar]`, brand base colors, radii); `komponenten.py`'s CSS layers the gradient, wordmark, typography, and other brand/structural details the native theme can't express. See "Brand & Design System" above.

## Testing approach

There is no automated test suite (no pytest or similar configured) and no lint/format tooling. Verification is manual:

- For any UI/feature change, run `streamlit run web_app.py` and exercise the actual feature in-browser — golden path and at least one edge case (e.g. no documents, one document, several documents, deleting an active document mid-chat).
- `python api_test.py` sanity-checks OpenAI connectivity/credentials in isolation.
- Several modules are deliberately kept pure and Streamlit-independent specifically so they're easy to test in isolation later even though no tests exist yet: `dokumentbibliothek.py` (filter/sort), `quellen.py` (source formatting), `dokument_verarbeitung.py`'s `text_in_chunks_aufteilen` (chunking). When adding non-trivial logic, prefer putting it in a pure module like these over embedding it directly in `web_app.py`'s widget code, for the same reason.

## API cost-awareness rules

The codebase has several deliberate patterns to keep OpenAI usage cheap; preserve them when extending the app:

- **Batch embeddings, never loop per-chunk.** A newly uploaded document's chunks are embedded in one call (`retrieval.embeddings_batch_erstellen`), not one call per chunk.
- **Cap chunks per document, and scale the cap down as document count grows.** Chat uses 4 chunks/doc for a single active document, else `max(2, 8 // anzahl_dokumente)`; Analyse & Vergleich uses 8/doc; a single Dokument-prüfen category uses 6/doc; `kompletter_check` uses 12/doc but folds all categories into one call (see below) rather than multiplying calls.
- **Prefer one combined model call over N smaller ones.** `pruefung.kompletter_check` runs all six categories through a single combined search query and a single Responses API call instead of six independent (6x cost) calls — follow this pattern for any new "run everything" action.
- **Keep answers terse by instruction, not just for readability.** `ki_analyse.KUERZE_HINWEIS` (bullets/tables over prose, no filler) caps output tokens on every AI-analysis feature; `pdf_logik.frage_beantworten`'s system prompt follows the same idea for Chat.
- **Never spend a model call on something a heuristic can do.** Chat titles are derived with a plain string function (`speicher._kurztitel_erzeugen`), not an API call.
- **Dedup uploads by content hash before parsing/embedding** (`speicher.dokument_nach_hash`) so re-uploading an unchanged file never re-spends embedding cost.
- When adding a new AI-analysis feature, build it on `ki_analyse.py` (fixed/topical search query + capped per-document chunk count + one model call) rather than inventing a new retrieval/prompt/API pattern.

## Git workflow

- Commit messages are short, English, imperative-mood summaries of the change (e.g. "Add multi-format document support and library workspace") — this is the current convention; do not revert to the earlier German-language commit style still visible further back in history.
- `app_daten/` (the SQLite database and original uploaded files) is gitignored and must never be committed.
- Follow the repo-wide instruction below: never commit or push automatically unless explicitly asked.

## Development workflow

Claude Code is the primary implementation agent for this project. ChatGPT is used for planning, architecture decisions, and code review.

When implementing a task:

1. First inspect the relevant existing files and understand the current behavior before making changes.
2. Preserve existing working functionality unless the task explicitly requires changing it.
3. Prefer modifying existing code over unnecessary rewrites.
4. Keep implementations simple and appropriate for the current size of the project.
5. Do not introduce new frameworks, dependencies, services, or architectural patterns unless they provide a clear benefit for the requested feature.
6. If the same relevant logic exists in both `app.py` and `web_app.py`, keep both implementations consistent unless the task explicitly concerns only one version.
7. Never expose, print, commit, or hard-code API keys, credentials, or secrets.
8. After implementing a task, inspect the changed files for obvious errors and run appropriate available checks when possible.
9. Clearly summarize:
   - which files were changed,
   - what was changed,
   - any important design decisions,
   - what was tested,
   - and any remaining limitations or risks.
10. Do not commit changes automatically unless explicitly requested.

## Collaboration with ChatGPT

Tasks may originate from a development specification prepared by ChatGPT.

When such a specification is provided:

- Treat its functional requirements and acceptance criteria as the target.
- You may improve implementation details when this produces cleaner or safer code.
- Do not silently omit requested functionality.
- If a requirement conflicts with the existing codebase, explain the conflict before making a destructive change.
- Keep changes reviewable so the resulting files can subsequently be reviewed by ChatGPT.

## Autonomous development workflow

When I request a feature or change, handle the complete development workflow yourself.

Process:

1. Understand the requested feature and inspect the relevant existing code.
2. Create a short implementation plan internally before editing files.
3. Implement the feature directly in the project.
4. Preserve existing working functionality unless a change is explicitly required.
5. Run appropriate syntax checks and tests.
6. If a test fails, diagnose and fix the issue automatically.
7. Repeat testing until the implementation works or a real blocker is reached.
8. Do not ask unnecessary questions when a reasonable implementation decision can be made independently.
9. Do not commit or push automatically unless explicitly requested.
10. At the end, provide a concise summary of:
   - what was implemented,
   - files changed,
   - tests performed,
   - remaining issues, if any.

For larger features, prefer implementing the feature completely rather than stopping after planning.
