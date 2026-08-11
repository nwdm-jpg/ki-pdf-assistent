# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

KI-PDF-Assistent is a German-language, AI-powered PDF question-answering tool. A user uploads/selects a PDF, asks a question, and the app finds the most relevant pages (simple keyword overlap, no embeddings) and sends only those pages to an OpenAI model to generate a grounded answer. There is no backend framework, database, or test suite — this is a small, single-purpose personal project.

## Commands

There is no `requirements.txt`/`pyproject.toml` in the repo; dependencies exist only in the local `.venv`. Known dependencies (from `.venv`): `openai`, `pypdf`, `streamlit`.

```bash
# Activate the existing venv (Windows)
.venv\Scripts\activate

# Run the Streamlit web app (primary entry point)
streamlit run web_app.py

# Run the CLI version (reads a PDF from the pdfs/ folder, prompts interactively)
python app.py

# Sanity-check the OpenAI API connection/credentials
python api_test.py
```

An `OPENAI_API_KEY` must be available in the environment (the `openai.OpenAI()` client picks it up automatically) for any of the above to work.

There are no lint, format, or test commands configured in this repo.

## Architecture

`app.py` and `web_app.py` are entry points that share their PDF/retrieval/OpenAI logic via `pdf_logik.py`; UI-specific orchestration (print statements, Streamlit widgets, spinners, try/except boundaries) stays in each entry point:

- **`web_app.py`** — Streamlit UI (main entry point). Multi-document, conversational chat assistant: upload any number of PDFs at once (sidebar), ask questions that search across all of them, and get answers grounded in the retrieved excerpts with filename+page sources. Chat history lives in `st.session_state.chat_verlauf`; uploaded documents (each already extracted into page-level entries) live in `st.session_state.dokumente`, keyed by `(dateiname, size)` so re-running the script doesn't re-parse unchanged files and removing a file from the uploader drops it from state.
- **`app.py`** — CLI equivalent for a *single* PDF: loads a file by filename from `pdfs/`, loops on stdin questions until the user types `ende`. No multi-document or conversational-history features — each question is answered independently, matching its original behavior.
- **`pdf_logik.py`** — shared module: PDF text extraction, keyword-based multi-document page retrieval, source formatting, and the OpenAI call (with optional chat-history threading). Holds the module-level `client = OpenAI()` (env-based key handling), `STOPPWOERTER`, and `MODELL`.
- **`api_test.py`** — minimal standalone script to verify the OpenAI API key/connection works.

### Data model: Seiteneintrag

A **Seiteneintrag** is a dict `{"dateiname": str, "seitennummer": int, "text": str}` — one page of one document. Multi-document search is just a flat list of Seiteneintrag dicts pooled from every uploaded file (see `web_app.py`'s `alle_seiten`); the single-document CLI case is simply a pool containing one document's pages.

### Question-answering pipeline (`pdf_logik.py`)

1. `pdf_seiten_extrahieren(reader, dateiname)` — extract text per page from an already-opened `pypdf.PdfReader` → list of Seiteneintrag dicts for that one document (pages with no extractable text are skipped).
2. `relevante_seiten_ermitteln(frage, seiten, anzahl=3, zusatzkontext="")` — tokenize the question (+ optional `zusatzkontext`), strip German stopwords (`STOPPWOERTER`), and score every page by the size of the intersection between question words and page words (simple bag-of-words overlap — no embeddings/vector search). **Scores and selects the top `anzahl` pages per document, not a single global top-N** — this guarantees every uploaded document gets a chance to appear in the context, even if a prior chat turn's vocabulary biases scoring toward one document (see the follow-up scenario below). `web_app.py` passes the recent chat history as `zusatzkontext` so vocabulary-poor follow-ups still retrieve something relevant, and scales `anzahl` down as the document count grows to keep the prompt bounded.
3. `relevanten_text_zusammenstellen(beste_seiten)` — builds the prompt text, with each excerpt labeled `--- {dateiname}, Seite {n} ---`.
4. `verwendete_quellen(beste_seiten)` — extracts `(dateiname, seitennummer)` pairs from the selected pages.
5. `frage_beantworten(frage, relevanter_text, verlauf=None)` — calls `client.responses.create(model=MODELL, ...)` (OpenAI Responses API, not Chat Completions) with a system prompt instructing the model to answer *only* from the provided pages. When `verlauf` (a list of `{"frage", "antwort"}` dicts from the current chat) is given, prior turns are threaded in as alternating user/assistant messages and the system prompt gains an extra clause: use the history only to resolve references ("im zweiten Vertrag"), never as an additional knowledge source. `app.py` never passes `verlauf`, so its system prompt and behavior are unchanged from the single-document version.
6. `formatiere_quellenhinweis(quellen)` — formats `(dateiname, seitennummer)` pairs (from `verwendete_quellen`) into a German source string for display: groups by filename, dedups and sorts page numbers, and picks singular ("Seite 3") vs. plural ("Seiten 3, 7") wording per file, e.g. `"Quellen: vertrag_a.pdf (Seite 1); vertrag_b.pdf (Seiten 3, 7)"`. Returns `""` for no sources. Used by `web_app.py` below each chat answer; `app.py` derives its own plain page-number list from `verwendete_quellen` for its console output and does not use this formatter.

Conversational follow-ups: `web_app.py` passes the last 2 turns' text as `zusatzkontext` to retrieval and the last 6 turns as `verlauf` to `frage_beantworten`, both scoped to `st.session_state.chat_verlauf` (cleared by the "Chat leeren" button, never persisted beyond the session).

### Language convention

All UI strings, variable/function names, and prompts are in German (e.g. `frage` = question, `antwort` = answer, `seiten` = pages, `dokumente` = documents). Keep new code consistent with this convention.

## Files

- `pdfs/` — local folder used by `app.py` as the source for PDFs by filename; contains a sample test PDF.


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