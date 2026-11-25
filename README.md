# ✈ Skyward

**Flight price intelligence: ML fare forecasting, deal detection and price alerts.**

Skyward tracks airfares, learns how prices move, and tells you *when* to book. A gradient-boosted model
predicts what a fare will cost on every day between now and departure, so you get a clear
**BUY / WAIT** call, the expected lowest price, and the best day to buy. Set a watch and Skyward
notifies you by email or Telegram when it's time.

![CI](https://github.com/Faryaan-Khan/skyward/actions/workflows/ci.yml/badge.svg)

## Features

- **Fare forecasting**: learns route cost, monthly seasonality, day-of-week effects, holiday and
  recurring-event demand (Chinese New Year, Hari Raya, Christmas, summer peak…) and the U-shaped
  days-to-departure curve.
- **Buy-or-wait advice**: sweeps every possible purchase day and reports the expected low, the best
  buy date and the model's error band (MAE on a held-out split).
- **Deals & special offers**: flags recent fares more than 15% under the model's expected price,
  including detected flash sales.
- **Price alerts**: watch a trip with an optional target price; alerts go out by email (SMTP) or
  Telegram bot, both free.
- **Dashboard**: forecast curve, price history, deals feed and alert management.
- **REST API** with interactive docs at `/docs`.

## Quick start

```bash
pip install -r requirements.txt
cd backend
uvicorn app.main:app --reload
# open http://localhost:8000
```

On first run Skyward seeds two years of synthetic fare history and trains the model (a few seconds).
No API keys are required.

Run the tests:

```bash
cd backend && python -m pytest ../tests -q
```

## How the model works

| Feature | Why it matters |
| --- | --- |
| Days to departure | Fares are high far out, bottom out ~6 to 8 weeks before, then climb steeply |
| Departure month, day of year (sin/cos) | Seasonal demand |
| Day of week | Friday/Sunday departures cost more |
| Event factor | Demand spikes around holidays and yearly events |
| Route | Base cost per city pair |

The target is log price, trained with scikit-learn's `HistGradientBoostingRegressor`. Flash-sale
observations are excluded from training so the model learns the normal market, which is what lets it
spot deals as outliers.

## Data sources (honest version)

"Every source" isn't realistic for a free project. Airline and OTA pricing is mostly behind paid
GDS contracts or terms of service that forbid scraping. Skyward is built to plug in **free** sources:

| Source | Status |
| --- | --- |
| Synthetic history generator | Built in, used by default so the demo works anywhere |
| [Amadeus Self-Service](https://developers.amadeus.com) (free test tier) | Adapter included in `backend/app/sources/amadeus.py`; set keys in `.env` |
| Other free APIs / public fare datasets | Add a module under `sources/` returning `{price, carrier, source}` |

Synthetic data is clearly labelled in the UI. Forecasts on synthetic data demonstrate the method, not
real market predictions.

## Notifications

Copy `.env.example` to `.env` and fill in what you want:

- **Email**: any SMTP account (e.g. a Gmail app password).
- **Telegram**: create a bot with @BotFather, set `TELEGRAM_BOT_TOKEN`, and use your chat ID on the watch.

Trigger checks on a schedule (cron, or a GitHub Actions `schedule`) with
`POST /api/watches/check`.

## API

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/routes` | Supported routes |
| GET | `/api/history/{route}` | Weekly low/average fare history |
| GET | `/api/forecast/{route}?depart=YYYY-MM-DD&current_price=` | Buy/wait advice and price curve |
| GET | `/api/deals` | Fares well below expected price |
| GET/POST/DELETE | `/api/watches` | Manage price watches |
| POST | `/api/watches/check` | Evaluate watches and send alerts |
| GET | `/api/alerts` | Alert log |
| GET | `/api/events` | Holiday / event calendar used by the model |

## Project structure

```
backend/app/
  main.py        FastAPI app and routes
  model.py       Forecasting model and buy/wait logic
  synthetic.py   Fare history generator (seasonality, events, booking curve)
  notify.py      Email and Telegram alerts
  db.py          SQLite storage
  sources/       Live fare adapters (Amadeus)
frontend/        Dashboard (vanilla JS + Chart.js)
tests/           API tests
```

## Roadmap

- Scheduled live ingestion from Amadeus into the history table
- Web push notifications
- Per-route models with uncertainty intervals (quantile regression)
- Multi-city and flexible-date search

## License

MIT
