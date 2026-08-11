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

`app.py` and `web_app.py` are thin entry points that share their PDF/retrieval/OpenAI logic via `pdf_logik.py`; UI-specific orchestration (print statements, Streamlit widgets, spinners, try/except boundaries) stays in each entry point:

- **`web_app.py`** — Streamlit UI (main entry point). Handles PDF upload, extraction, chat-style Q&A, and keeps chat history in `st.session_state.chat_verlauf`.
- **`app.py`** — CLI equivalent of the same flow: loads a PDF by filename from `pdfs/`, loops on stdin questions until the user types `ende`.
- **`pdf_logik.py`** — shared module: PDF text extraction, keyword-based page retrieval, and the OpenAI call. Holds the module-level `client = OpenAI()` (env-based key handling), `STOPPWOERTER`, and `MODELL`.
- **`api_test.py`** — minimal standalone script to verify the OpenAI API key/connection works.

### Question-answering pipeline (`pdf_logik.py`)

1. `pdf_seiten_extrahieren(reader)` — extract text per page from an already-opened `pypdf.PdfReader` → `(gesamter_text, seiten_texte)`, where `seiten_texte` is a list of `(page_number, page_text)`.
2. `relevante_seiten_ermitteln(frage, seiten_texte, anzahl=3)` — tokenize the question, strip German stopwords (`STOPPWOERTER`), and score every page by the size of the intersection between question words and page words (simple bag-of-words overlap — no embeddings/vector search). Returns the top `anzahl` scoring pages.
3. `relevanten_text_zusammenstellen(beste_seiten)` / `verwendete_seitennummern(beste_seiten)` — build the prompt text and the list of page numbers used, respectively.
4. `frage_beantworten(frage, relevanter_text)` — calls `client.responses.create(model=MODELL, ...)` (OpenAI Responses API, not Chat Completions) with a system prompt instructing the model to answer *only* from the provided pages and to say clearly when the answer isn't in the text.

Both `app.py` and `web_app.py` call these functions in the same order (extract → score → build prompt text → call model) but wrap them with their own I/O (console prints vs. `st.chat_message`/`st.spinner`/`st.session_state`) — a change to retrieval/scoring/prompt logic only needs to happen in `pdf_logik.py`; a change to how results are presented still belongs in the individual entry point.

### Language convention

All UI strings, variable/function names, and prompts are in German (e.g. `frage` = question, `antwort` = answer, `seiten` = pages, `gesamter_text` = full text). Keep new code consistent with this convention.

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