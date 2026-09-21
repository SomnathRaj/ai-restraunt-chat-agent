# AI Restaurant Chat Ordering Agent

A web-based conversational ordering system: customers browse the menu, get recommendations, place orders, and check order status entirely through chat (English, Bengali, Hinglish, Benglish — auto-detected). Flask + MongoDB + Google Gemini, no Docker.

See [AI_Restaurant_Chat_Ordering_Agent_PRD.md](AI_Restaurant_Chat_Ordering_Agent_PRD.md) for the full product spec, [ARCHITECTURE.md](ARCHITECTURE.md) for the technical design, and [CHECKLIST.md](CHECKLIST.md) for what's implemented and verified so far.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Configure

```bash
cp .env.example .env
```

Fill in `.env`:

| Variable | Required | Notes |
|---|---|---|
| `MONGODB_URI` | for DB features | The app boots fine without it — `/healthz` reports `"mongodb": false` until set |
| `MONGODB_DATABASE` | no | Defaults to `restaurant_bot` |
| `GEMINI_API_KEY` | for chat | The app boots fine without it — `/healthz` reports `"gemini": false` until set |
| `GEMINI_MODEL` | no | Defaults to the `-latest` Flash alias |
| `FLASK_SECRET_KEY` | for anything beyond local dev | Generate one: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `CORS_ALLOWED_ORIGINS` | no | Keep in sync with `PORT` |
| `PORT` | no | Defaults to `5000` |
| `RESTRAUNT_NAME` | no | Shown in the chat UI header/title |

## Load sample data

Once `MONGODB_URI` is set:

```bash
python scripts/seed_menu.py   # 11 sample menu items across 6 categories
python scripts/seed_faq.py    # 8 starter FAQ entries (hours, parking, payments, etc.)
```

Both are safe to re-run — they upsert by `item_id`/`faq_id`.

## Run

```bash
python run.py
```

Opens at `http://localhost:<PORT>` (default `http://localhost:5000`). Check `curl localhost:<PORT>/healthz` to confirm Mongo/Gemini are both connected — `{"mongodb": true, "gemini": true}`.

## Test

```bash
pytest -q
```

Runs against `mongomock` (no real database needed) — fast and hermetic. No `GEMINI_API_KEY` needed either; AI-loop tests use a scripted `FakeGeminiClient`.

## Project layout

```
app/
├── api/        # thin HTTP layer (Flask blueprints) — no business logic
├── services/   # all business logic — the single source of truth for
│               # both the REST paths above and the AI tool-call path below
├── ai/         # Gemini tool-calling loop, tool declarations, system prompt
├── models/     # MongoDB connection + index setup
├── utils/      # validation, sanitization, error handling
├── static/     # plain CSS/JS, no build step
└── templates/  # the chat UI shell
scripts/        # seed_menu.py, seed_faq.py
tests/          # pytest suite (mongomock + FakeGeminiClient — no live creds needed)
```

The one rule that matters most throughout: **the REST `[Add]`-button path and the AI tool-call path always call the same `app/services/*` function.** Neither path re-implements business logic independently — see `ARCHITECTURE.md` for why.
