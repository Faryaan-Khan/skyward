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
[![License: PolyForm Noncommercial](https://img.shields.io/badge/license-PolyForm%20Noncommercial-blue?style=flat-square)](LICENSE)

[**Open the live site →**](https://skyward-tv5y.onrender.com) &nbsp;·&nbsp; [Releases](https://github.com/Faryaan-Khan/skyward/releases) &nbsp;·&nbsp; [Security](SECURITY.md)

<img src="docs/screenshot.png" alt="Skyward dashboard at night: flight search, great-circle route on a globe, predicted price drops" width="820">

<details><summary>Day mode</summary>
<img src="docs/screenshot-light.png" alt="Skyward dashboard in day mode" width="820">
</details>

</div>

Search flights between any two cities in the world, see live fares in your own currency, compare the
big booking sites in one click, and let machine learning tell you *when* to book. A gradient-boosted
model predicts what a fare will cost on every day between now and departure, so you get a clear
**BUY / WAIT** call, the expected lowest price and the best day to buy, and a feed of fares it expects
to **drop**. Set a watch and Skyward notifies you by email or Telegram when it's time.

> The live site runs on Render's free plan. A [cron-job.org](https://cron-job.org) job pings it every
> 10 minutes so it stays awake and keeps refreshing prices; GitHub Actions also pings it as a backup.

## Tech stack

| Layer | Tools |
| --- | --- |
| API & web | FastAPI, Uvicorn, vanilla JS, Chart.js, globe.gl (three.js) for the 3D earth |
| Data | PostgreSQL (Neon) via SQLAlchemy, SQLite for local dev |
| ML | scikit-learn `HistGradientBoostingRegressor`, pandas, NumPy |
| Live prices | Travelpayouts / Aviasales Data API |
| Hosting & automation | Render; GitHub Actions runs the tests on every push and deploys to Render only when they pass |

## Features

- **Worldwide search**: type any city or airport (autocomplete covers every IATA code). The cheapest
  real fare for each date of the month appears in seconds; routes people search are then refreshed
  automatically every 15 minutes.
- **Book at the source**: every real price links to that exact offer on Aviasales (a metasearch over
  hundreds of airlines and agencies). "Compare" opens the same search on the operating airline's site,
  Google Flights, Skyscanner, Trip.com, Booking.com, Expedia, Kayak, Cheapflights and Momondo. Skyward
  never takes payments.
- **Interactive 3D earth**: NASA imagery (city lights at night), drag to spin, zoom, and tap any of
  ~3,700 airports to fly from or to it.
- **Popular this week**: recomputed weekly from Skyward searches plus worldwide demand from 15 hubs.
- **Compare airlines**: every airline found on the route (cheapest and direct flights), each with its exact
  fare and the airline's own website; 180+ airlines in the directory.
- **Fast live prices**: active routes refresh about every 2 minutes; the page re-checks every minute.
- **How it works**: a simple animated guide at [`/how`](https://skyward-tv5y.onrender.com/how).
- **Any currency**: prices shown in 160+ currencies with daily exchange rates; your choice is remembered.
- **Predicted price drops**: the model scans live fares and highlights the ones it expects to get
  cheaper, by how much, and the best day to buy.
- **Buy-or-wait advice**: sweeps every possible purchase day and reports the expected low, the best
  buy date and the model's error band (MAE on a held-out split).
- **Deals**: real fares more than 15% under the model's expected price for that route and date.
- **Price alerts that don't spam**: email (double opt-in) or Telegram (one tap). Alerts fire only on
  real, buyable prices, at most once a day per trip, only after a further 5% drop, 8 per trip max,
  with one-click unsubscribe. Alerts are private to the browser that created them.
- **Luxury dashboard**: dark charcoal and gold, plus a soft day mode (the sun/moon toggle morphs and
  the page cross-fades). Works on phones.
- **Secure by default**: see [SECURITY.md](SECURITY.md).

### Setting up alerts (owner)
- **Email**: create a Gmail account for the app (e.g. skyward.alerts@gmail.com), turn on 2-Step
  Verification, create an *App password*, then in Render set `SMTP_USER` (the address) and
  `SMTP_PASSWORD` (the app password). `SMTP_HOST`/`SMTP_PORT` default to Gmail.
- **Telegram**: message @BotFather → `/newbot` → copy the token into Render as `TELEGRAM_BOT_TOKEN`.
  The webhook registers itself on the next deploy.

Developer API docs are available locally with `ENABLE_DOCS=1` (they're off on the public site).

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
   copy your site URL (e.g. `https://skyward-xxxx.onrender.com`). For deploys after passing tests,
   copy the service's **Deploy Hook** (Settings) into a GitHub secret named `RENDER_DEPLOY_HOOK`.
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
| Route base (log) | The route's typical (median) fare, so one model covers every route in the world |

The target is the log of *price relative to the route's typical fare*, trained with scikit-learn's
`HistGradientBoostingRegressor` on synthetic baseline history plus every live snapshot collected so far.
Because the model learns movement relative to a route's own level, a brand-new route only needs one batch
of live prices before it can be forecast. **Predicted drops** come from the same model: for each live fare
it sweeps the remaining purchase days and reports the expected fall from today's level and the best day. Flash-sale observations are excluded from training so the model learns the
normal market, which is what lets it spot deals as outliers.

**Calibration.** The synthetic history provides the *shape* of prices (seasonality, holidays, booking
curve), and the live fares set the *level*. Before each training run, every route's synthetic prices
are rescaled so their median matches the median real fare over the same booking horizon. The price-history
chart uses the same scaling. Without this step the model expected fares about 2–3× too high and flagged
nearly every real fare as a "60% off" deal. With it, the model's error (MAE) dropped from about $20 to about $8.

## API

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/search?origin=KUL&destination=LHR&month=2027-01` | Live fares for any city pair for one month, with model view per date |
| GET | `/api/places?q=tokyo` | Worldwide city and airport autocomplete |
| GET | `/api/drops` | Fares the model expects to fall, by how much and when to buy |
| GET | `/api/rates` | USD exchange rates for 160+ currencies |
| GET | `/api/status` | Live or demo mode, last refresh time, rows by source |
| GET | `/api/routes` | Featured and most-searched routes |
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
  sources/       Travelpayouts fares + place search, exchange rates
frontend/        Dashboard (vanilla JS + Chart.js, SVG globe)
tests/           API tests
render.yaml      Render deployment blueprint
.github/workflows/ci.yml        tests on every push
.github/workflows/deploy.yml    deploy to Render after tests pass
.github/workflows/refresh.yml   backup 15-minute wake-up
CHANGELOG.md     release notes per version
```

## Versions

Releases follow [semantic versioning](https://semver.org) and are listed on the
[Releases page](https://github.com/Faryaan-Khan/skyward/releases); see [CHANGELOG.md](CHANGELOG.md).

## Roadmap

- Return trips and flexible-date search
- Web push notifications
- Uncertainty bands on forecasts (quantile regression)

## License

Copyright (c) 2026 Faryaan Khan.

Skyward is **source-available** under the [PolyForm Noncommercial License 1.0.0](LICENSE). You're welcome
to read the code, learn from it, and run or modify it for personal, educational or other non-commercial
purposes, as long as you keep the copyright notice. **Commercial use is not permitted** without a separate
written licence; open an issue if you're interested.

Releases up to and including v2.2.0 were published under the MIT License, and those copies stay under MIT.

Third-party data, imagery and libraries are credited in [NOTICE.md](NOTICE.md). The live site's
[terms and privacy page](frontend/legal.html) explains affiliate links and what the price alerts store.
