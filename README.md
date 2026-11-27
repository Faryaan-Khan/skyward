<div align="center">

# ✈ Skyward

**Live flight prices, ML fare forecasts and price alerts: know when to book.**

[![Live demo](https://img.shields.io/badge/live%20demo-skyward--tv5y.onrender.com-2ecc8f?style=flat-square)](https://skyward-tv5y.onrender.com)
[![CI](https://img.shields.io/github/actions/workflow/status/Faryaan-Khan/skyward/ci.yml?branch=main&label=tests&style=flat-square)](https://github.com/Faryaan-Khan/skyward/actions/workflows/ci.yml)
[![Price refresh](https://img.shields.io/github/actions/workflow/status/Faryaan-Khan/skyward/refresh.yml?branch=main&label=price%20refresh&style=flat-square)](https://github.com/Faryaan-Khan/skyward/actions/workflows/refresh.yml)
![Python](https://img.shields.io/badge/python-3.12-3776ab?style=flat-square&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169e1?style=flat-square&logo=postgresql&logoColor=white)
![scikit-learn](https://img.shields.io/badge/scikit--learn-f7931e?style=flat-square&logo=scikitlearn&logoColor=white)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue?style=flat-square)](LICENSE)

[**Open the live site →**](https://skyward-tv5y.onrender.com) &nbsp;·&nbsp; [API docs](https://skyward-tv5y.onrender.com/docs)

<img src="docs/screenshot.png" alt="Skyward dashboard: buy/wait forecast, live fares table, price history and deals" width="820">

</div>

Skyward pulls real fares every 15 minutes, learns how prices move, and tells you *when* to book. A
gradient-boosted model predicts what a fare will cost on every day between now and departure, so you
get a clear **BUY / WAIT** call, the expected lowest price and the best day to buy. Set a watch and
Skyward notifies you by email or Telegram when it's time.

> The live site runs on Render's free plan, so the first visit after a quiet spell can take up to a
> minute to wake up.

## Tech stack

| Layer | Tools |
| --- | --- |
| API & web | FastAPI, Uvicorn, vanilla JS + Chart.js |
| Data | PostgreSQL (Neon) via SQLAlchemy, SQLite for local dev |
| ML | scikit-learn `HistGradientBoostingRegressor`, pandas, NumPy |
| Live prices | Travelpayouts / Aviasales Data API |
| Hosting & automation | Render (auto-deploy from `main`), GitHub Actions (tests + 15-minute refresh) |

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
 GitHub Actions (every 15 min)        Render web service (N workers / instances)
 ┌────────────────────────┐           ┌──────────────────────────────────────────┐
 │ wake site +            │──POST──▶  │ FastAPI + dashboard (stateless)          │
 │ POST /api/refresh      │           │ background refresh, one at a time via a  │
 └────────────────────────┘           │ DB lock: fetch fares → retrain → alerts  │
                                      │ reloads newest model; 60s cache + gzip   │
                                      └──────────────────┬───────────────────────┘
                                                         │
                                                  Postgres (Neon)
```

- **Refreshes never block visitors.** Fetching and training run in a background thread, and a
  lock row in the database makes sure only one server runs each 15-minute refresh, however many
  servers there are. `python -m app.ingest` runs the same refresh from the command line.
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

## Deploy your own (free)

You need three free accounts. Keep tokens and passwords in the services' settings, never in code.

1. **Travelpayouts** (live prices): sign up at travelpayouts.com, join the **Aviasales** program,
   then copy your **API token** from your profile. Your **marker** (partner ID) is optional and
   earns commission on "Book" clicks.
2. **Neon** (database): sign up at neon.tech, create a project, and copy the **connection string**
   (starts with `postgresql://…?sslmode=require`).
3. **Render** (website): sign up at render.com with GitHub, give Render's GitHub app access to the
   repo, then **New → Blueprint** → choose the repo. It reads `render.yaml`. When asked, paste
   `DATABASE_URL` (Neon), `TRAVELPAYOUTS_TOKEN` and optionally `TRAVELPAYOUTS_MARKER`. Deploy, then
   copy your site URL (e.g. `https://skyward-xxxx.onrender.com`). Every push to `main` now redeploys
   automatically.
4. **GitHub** (keeps it refreshing while nobody is visiting): in this repo, go to Settings → Secrets and
   variables → Actions → **Variables** and add `SITE_URL` = your Render URL. No secrets needed: the
   site fetches prices itself, and the workflow only wakes it every 15 minutes.

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

**Calibration.** The synthetic history provides the *shape* of prices (seasonality, holidays, booking
curve), and the live fares set the *level*. Before each training run, every route's synthetic prices
are rescaled so their median matches the median real fare over the same booking horizon. The price-history
chart uses the same scaling. Without this step the model expected fares about 2–3× too high and flagged
nearly every real fare as a "60% off" deal. With it, the model's error (MAE) dropped from about $20 to about $8.

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
