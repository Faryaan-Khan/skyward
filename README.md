# ✈ Skyward

**Live flight prices, ML fare forecasting, deal detection and price alerts.**

![CI](https://github.com/Faryaan-Khan/skyward/actions/workflows/ci.yml/badge.svg)

Skyward pulls real fares every 15 minutes, learns how prices move, and tells you *when* to book. A
gradient-boosted model predicts what a fare will cost on every day between now and departure, so you
get a clear **BUY / WAIT** call, the expected lowest price and the best day to buy. Set a watch and
Skyward notifies you by email or Telegram when it's time.

## Features

- **Live fares**: cheapest real fare per departure date for the next 6 months, refreshed every
  15 minutes, with the change since yesterday and how it compares to the model's expected price.
- **Fare forecasting**: learns route cost, monthly seasonality, day-of-week effects, holiday and
  recurring-event demand (Chinese New Year, Hari Raya, Christmas, summer peak…) and the U-shaped
  days-to-departure curve.
- **Buy-or-wait advice**: sweeps every possible purchase day and reports the expected low, the best
  buy date and the model's error band (MAE on a held-out split).
- **Deals & special offers**: real fares more than 15% under the model's expected price.
- **Private price alerts**: each watch gets a secret manage token kept in the visitor's browser, so
  nobody can see anyone else's watches or email address. Alerts go out at most once a day per watch.
- **Dashboard** that refreshes itself every minute, and a **REST API** with docs at `/docs`.

## Architecture (built to scale)

```
 GitHub Actions (every 15 min)            Render web service (N workers / instances)
 ┌──────────────────────────┐             ┌───────────────────────────────────┐
 │ python -m app.ingest     │             │ FastAPI + dashboard               │
 │  1. fetch Travelpayouts  │──writes──▶  │  stateless, reads Postgres        │
 │  2. retrain model        │   Postgres  │  reloads newest model every 2 min │
 │  3. send due alerts      │   (Neon)    │  60s in-memory cache + gzip       │
 └──────────────────────────┘  ◀──reads── └───────────────────────────────────┘
```

- **Web and data work are separate.** Fetching prices and training never slow down the website.
- **Stateless web tier.** All state is in Postgres, so you scale by adding workers or instances
  (`WEB_CONCURRENCY`, Render plan / instance count). No code changes needed.
- **The model is stored in the database**, so every worker serves the same, latest model.
- **Small database.** Each refresh replaces today's snapshot per route instead of appending, so the
  free Neon tier lasts a long time; `fetched_at` still records the exact refresh time.

## Data sources (honest version)

| Source | Status |
| --- | --- |
| [Travelpayouts / Aviasales Data API](https://support.travelpayouts.com/hc/en-us/articles/203956163) | **Live source.** Free with a Travelpayouts account. Real fares that Aviasales users found in roughly the last 48 hours |
| Synthetic history generator | Seeds two years of baseline history so the model works from day one. Labelled "demo" in the UI |

Limits worth knowing:
- Prices are **cached from real searches**, not quoted live from airlines, so a fare may have changed
  by the time you click through, and quiet routes or dates can have gaps.
- "Real-time" here means every 15 minutes. True second-by-second airline pricing is only sold through
  paid GDS contracts. (Amadeus's free Self-Service API, used in an earlier version, shut down on
  17 July 2026.)
- Prices are in USD.

## Go live (free): step by step

You need three free accounts. Do the sign-ups yourself; never paste passwords or tokens into code.

1. **Travelpayouts** (live prices): sign up at travelpayouts.com, join the **Aviasales** program,
   then copy your **API token** from your profile. Your **marker** (partner ID) is optional and
   earns commission on "Book" clicks.
2. **Neon** (database): sign up at neon.tech, create a project, and copy the **connection string**
   (starts with `postgresql://…?sslmode=require`).
3. **Render** (website): sign up at render.com with GitHub → **New → Blueprint** → choose this repo.
   It reads `render.yaml`. When asked, paste `DATABASE_URL` (Neon), `TRAVELPAYOUTS_TOKEN` and
   optionally `TRAVELPAYOUTS_MARKER`. Deploy, then copy your site URL (e.g. `https://skyward-xxxx.onrender.com`).
4. **GitHub** (price refresh every 15 min): in this repo, go to Settings → Secrets and variables → Actions.
   - **Secrets:** `DATABASE_URL`, `TRAVELPAYOUTS_TOKEN` (plus `TRAVELPAYOUTS_MARKER`, `SMTP_*` and
     `TELEGRAM_BOT_TOKEN` if you want alerts).
   - **Variables:** `SKYWARD_LIVE` = `true`, and `SITE_URL` = your Render URL.
   - Then Actions → **Refresh prices** → **Run workflow** once to load the first live prices.

Within about two minutes the site shows "Live prices · updated just now". Render's free plan sleeps
when idle; the refresh job pings the site every 15 minutes, which keeps it mostly awake.

**Scaling up later:** move Render to a paid plan and raise `WEB_CONCURRENCY` or the instance count,
and use Neon's connection-pooler URL (`-pooler` in the host name). If you need refreshes more often
than every 15 minutes, move the ingest job to a Render Cron Job or a background worker.

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
cd backend
..\.venv\Scripts\python -m uvicorn app.main:app --reload
# open http://localhost:8000
```

With no `DATABASE_URL`, Skyward uses a local SQLite file and synthetic data. To pull real prices
locally, set `TRAVELPAYOUTS_TOKEN` and run `python -m app.ingest` from `backend/`.

Run the tests:

```powershell
cd backend; ..\.venv\Scripts\python -m pytest ../tests -q
```

## How the model works

| Feature | Why it matters |
| --- | --- |
| Days to departure | Fares are high far out, bottom out ~6 to 8 weeks before, then climb steeply |
| Departure month, day of year (sin/cos) | Seasonal demand |
| Day of week | Friday/Sunday departures cost more |
| Event factor | Demand spikes around holidays and yearly events |
| Route | Base cost per city pair |

The target is log price, trained with scikit-learn's `HistGradientBoostingRegressor` on synthetic
baseline history plus every live snapshot collected so far, so the model learns more about real prices
the longer it runs. Flash-sale observations are excluded from training so the model learns the
normal market, which is what lets it spot deals as outliers.

## API

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/status` | Live or demo mode, last refresh time, rows by source |
| GET | `/api/routes` | Supported routes |
| GET | `/api/live/{route}` | Latest real fares per departure date, change vs yesterday |
| GET | `/api/history/{route}` | Weekly low/average fare history |
| GET | `/api/forecast/{route}?depart=YYYY-MM-DD&current_price=` | Buy/wait advice and price curve (uses the latest live fare if no price is given) |
| GET | `/api/deals` | Fares well below expected price |
| POST | `/api/watches` | Create a watch, returns a private manage token |
| GET | `/api/watches?tokens=a,b` | Your watches and their alerts |
| DELETE | `/api/watches/{token}` | Remove a watch |
| POST | `/api/watches/check` | Evaluate watches and send alerts (admin token) |
| POST | `/api/admin/reload-model` | Load the newest model now (admin token) |
| GET | `/api/events` | Holiday / event calendar used by the model |

Admin endpoints need the header `X-Admin-Token` matching `ADMIN_TOKEN` (Render generates one).

## Project structure

```
backend/app/
  main.py        FastAPI app, caching, model hot-reload
  ingest.py      Scheduled job: fetch live fares, retrain, send alerts
  model.py       Forecasting model and buy/wait logic
  db.py          SQLAlchemy tables (Postgres in production, SQLite locally)
  synthetic.py   Baseline history generator (seasonality, events, booking curve)
  notify.py      Email and Telegram alerts
  sources/       Live fare adapters (Travelpayouts)
frontend/        Dashboard (vanilla JS + Chart.js)
tests/           API tests
render.yaml      Render deployment blueprint
.github/workflows/refresh.yml   15-minute price refresh
```

## Roadmap

- More routes, chosen by what users watch most
- Web push notifications
- Per-route models with uncertainty intervals (quantile regression)
- Multi-city and flexible-date search

## License

MIT
