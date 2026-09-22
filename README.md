# AI Restaurant Chat Ordering Agent

A web-based conversational ordering system: customers browse the menu, get recommendations, place orders, and check order status entirely through chat (English, Bengali, Hinglish, Benglish — auto-detected). Flask + MongoDB + Google Gemini, no Docker. Includes a server-rendered Admin Portal for managing the menu, FAQ, and orders, plus a dashboard and chat session viewing.

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
| `FLASK_SECRET_KEY` | **required if `FLASK_ENV=production`** (the app refuses to boot without it there — the Admin Portal's login session depends on it) | Generate one: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `CORS_ALLOWED_ORIGINS` | no | Keep in sync with `PORT` |
| `PORT` | no | Defaults to `5000` |
| `RESTRAUNT_NAME` | no | Shown in the chat UI header/title |
| `UPI_ID` | no | Your UPI ID (VPA), e.g. `restaurant@okhdfcbank`. Without it, the invoice's UPI payment QR code is simply omitted (never rendered with a placeholder value) |

## Load sample data

Once `MONGODB_URI` is set:

```bash
python scripts/seed_categories.py  # menu category reference list (sourced by the Admin Portal's Category dropdown)
python scripts/seed_menu.py        # 42 sample menu items across 6 categories
python scripts/seed_faq.py         # 8 starter FAQ entries (hours, parking, payments, etc.)
python scripts/seed_admin_user.py  # creates the Admin Portal login (admin@gmail.com / pass123)
```

All four are safe to re-run — they upsert by `item_id`/`faq_id`/`name`/`email` respectively. Edit `ADMIN_EMAIL`/`ADMIN_PASSWORD` at the top of `seed_admin_user.py` before running it anywhere beyond local dev, since those two values are otherwise public (committed to this repo).

Optionally, for exercising the Admin Portal dashboard's date-range views with more than a single day of data:

```bash
python scripts/seed_demo_orders.py  # 10 demo orders backdated across the last 7 days -- NOT idempotent, adds 10 more each run
```

## Run

```bash
python run.py
```

Opens at `http://localhost:<PORT>` (default `http://localhost:5000`). Check `curl localhost:<PORT>/healthz` to confirm Mongo/Gemini are both connected — `{"mongodb": true, "gemini": true}`.

## Admin Portal

A server-rendered admin UI (same Flask app, no separate frontend/build) for running the restaurant without touching MongoDB directly. Requires `MONGODB_URI` and an admin account (`python scripts/seed_admin_user.py`, see above).

Open `http://localhost:<PORT>/admin/login`.

Covers: menu & FAQ management (search, pagination, active/inactive toggle); order management (status/payment updates, editable items & cooking instructions, printable Kitchen Tokens and invoices — with a UPI payment QR code — for a thermal printer); read-only chat session/transcript viewing (the session *list* and its search are structurally restricted to customer name/mobile only — message content is never exposed there, though it is visible on an individual session's own detail page); and a dashboard (sales/order-status/veg-nonveg charts, filterable by day/week/custom date range).

## Test

```bash
pytest -q
```

Runs against `mongomock` (no real database needed) — fast and hermetic. No `GEMINI_API_KEY` needed either; AI-loop tests use a scripted `FakeGeminiClient`.

## Project layout

```
app/
├── admin/      # server-rendered Admin Portal (Flask blueprints) — login,
│               # menu/FAQ CRUD, order management, read-only session viewing,
│               # dashboard; calls the same app/services/* functions as the
│               # customer-facing paths below
├── api/        # thin HTTP layer (Flask blueprints) — no business logic
├── services/   # all business logic — the single source of truth for
│               # both the REST paths above and the AI tool-call path below
├── ai/         # Gemini tool-calling loop, tool declarations, system prompt
├── models/     # MongoDB connection + index setup
├── utils/      # validation, sanitization, error handling
├── static/     # plain CSS/JS, no build step (static/admin/ for the Admin Portal)
└── templates/  # the chat UI shell (templates/admin/ for the Admin Portal)
scripts/        # seed_menu.py, seed_faq.py, seed_categories.py, seed_admin_user.py, seed_demo_orders.py
tests/          # pytest suite (mongomock + FakeGeminiClient — no live creds needed)
```

The one rule that matters most throughout: **the REST `[Add]`-button path and the AI tool-call path always call the same `app/services/*` function.** Neither path re-implements business logic independently — see `ARCHITECTURE.md` for why.
