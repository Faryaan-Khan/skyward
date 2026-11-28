# Changelog

All notable changes to Skyward. Versions follow [semantic versioning](https://semver.org).

## [2.2.0] - 2026-11-28

### Added
- **Compare airlines**: every airline found on a route this month, cheapest first, with its exact fare link
  and the airline's own website; each date's "Compare" menu lists that day's airlines. Direct flights are
  fetched too, so full-service and low-cost carriers appear side by side (e.g. 11 airlines London → New York).
- **How it works** page (`/how`) with simple animated steps, where prices come from, the forecast in plain
  words, privacy, and FAQs. It has its own link in the sidebar.
- Airline directory of 180+ airlines (Air Mauritius, Malaysia Airlines, Firefly, Fiji Airways, Air Seychelles…).

### Changed
- Live prices are faster: active routes (featured, watched, searched in the last 2 hours) refresh about every
  2 minutes; the open page re-checks every minute. Full refresh + model retraining stays every 15 minutes.
- Deals now have variety: best deal per route, one per destination, at most two per origin.
- Background auto-refreshes don't count as searches (keeps "Popular this week" honest).

## [2.1.0] - 2026-11-28

### Added
- Realistic interactive 3D earth (NASA imagery, night lights in dark mode): drag to spin, scroll/pinch to
  zoom, tap any of ~3,700 airports to set it as origin or destination.
- Weekly "Popular this week" routes: Skyward users' searches in the last 7 days combined with worldwide
  demand from 15 global hubs.
- More places to book: Trip.com, Booking.com and Cheapflights, plus the operating airline's own site.
- Real price alerts by email (double opt-in) and Telegram (one-tap connect), with anti-spam rules:
  real prices only, at most one per trip per day, a further 5% drop needed to repeat, 8 per trip max,
  5 emails per address per day, one-click unsubscribe in every message.
- Security: Content-Security-Policy, HSTS and other headers, per-visitor rate limits on all API routes,
  request size cap, hashed manage tokens, signed links, Subresource Integrity, CodeQL, pip-audit, Dependabot.

### Changed
- Softer, low-glare day palette; smoother day/night switch (no competing animations, charts recoloured in place).
- Every real fare figure (cheapest price, fares table, deals) links to that exact offer; predictions stay in-app.
- Deals sorted by biggest saving, showing the amount saved.
- Airline names instead of codes.
- The developer API page is no longer linked or public.

## [2.0.0] - 2026-11-28

### Added
- Worldwide search: any city or airport pair, with autocomplete for every IATA code.
- Compare & book links for every date: Aviasales, Google Flights, Skyscanner, Kayak, Expedia, Momondo.
- Prices in 160+ currencies with daily exchange rates (choice remembered per browser).
- Predicted price drops: fares the model expects to fall, by how much, and the best day to buy.
- Routes people search are tracked automatically and refreshed every 15 minutes (up to 40).
- Redesigned dashboard: dark charcoal and gold, animated great-circle flight path on a globe, day mode
  with a morphing sun/moon toggle and circular page cross-fade, mobile layout.
- Version shown in the app and the API (`/api/health`).

### Changed
- The forecasting model now learns price *relative to each route's typical fare*, so one model
  forecasts any route in the world (slightly more accurate on the same data: MAE 19.06 vs 19.65).
- Search endpoints are rate-limited per visitor to protect the free fare API quota.
- Deploys only happen after tests pass (GitHub Actions → Render deploy hook).

### Fixed
- Models saved by v1 are detected and retrained automatically on first start.

## [1.0.0] - 2026-11-27

First live version: live fares for 8 routes every 15 minutes, BUY/WAIT forecasts calibrated to real
prices, deals, private email/Telegram alerts, Postgres-backed scalable setup, CI.
