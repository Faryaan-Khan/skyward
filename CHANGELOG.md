# Changelog

All notable changes to Skyward. Versions follow [semantic versioning](https://semver.org).

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
