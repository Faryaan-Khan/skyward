# Notices and attributions

Skyward is Copyright (c) 2026 Faryaan Khan. The Skyward source code is licensed under the
[PolyForm Noncommercial License 1.0.0](LICENSE). Commercial use requires a separate written
licence from the copyright holder; open an issue on GitHub to ask.

Releases up to and including v2.2.0 were published under the MIT License. Copies obtained under
those releases remain available under MIT; everything after them is under the licence above.

The name "Skyward", the logo and the look of the site are part of this project and may not be used
to present another product as Skyward.

## Data and services

| Provider | Used for | Terms |
| --- | --- | --- |
| [Travelpayouts](https://www.travelpayouts.com) / [Aviasales](https://www.aviasales.com) Data API | Fares, airport and city data, place search | Travelpayouts affiliate terms. Fares are cached results of real searches and are not guaranteed. |
| [ExchangeRate-API](https://www.exchangerate-api.com) (open access) | Currency conversion | Attribution required; given in the site footer. |
| NASA Blue Marble and Black Marble imagery (via three-globe) | 3D earth day and night textures | Public domain. Credited as "Earth imagery: NASA". NASA does not endorse Skyward. |
| Telegram Bot API, SMTP email | Price alerts | Messages are sent only after the user opts in. |

Airline, airport and booking-site names (for example Aviasales, Google Flights, Skyscanner, Kayak,
Expedia, Trip.com and the airlines listed in Compare) are trademarks of their owners. They are used only
to label links to those sites. Skyward is not affiliated with or endorsed by any of them.

Links to Aviasales may carry an affiliate marker, so Skyward can earn a commission on a booking at no
extra cost to the traveller. Those links are marked `rel="sponsored"`.

## Design

The visual style was inspired by public "luxury aviation dashboard" concept work on Dribbble. No
artwork, code or assets were copied from it.

## Third-party software

| Component | Licence |
| --- | --- |
| [Chart.js](https://www.chartjs.org) | MIT |
| [globe.gl](https://github.com/vasturiano/globe.gl) and [three-globe](https://github.com/vasturiano/three-globe) (includes three.js) | MIT |
| [Inter](https://rsms.me/inter/) and [Cormorant Garamond](https://github.com/CatharsisFonts/Cormorant) via Google Fonts | SIL Open Font License 1.1 |
| [FastAPI](https://fastapi.tiangolo.com), [Pydantic](https://docs.pydantic.dev), [SQLAlchemy](https://www.sqlalchemy.org) | MIT |
| [Uvicorn](https://www.uvicorn.org), [HTTPX](https://www.python-httpx.org), [scikit-learn](https://scikit-learn.org), [pandas](https://pandas.pydata.org), [NumPy](https://numpy.org) | BSD-3-Clause |
| [psycopg](https://www.psycopg.org) | LGPL-3.0 (used unmodified, as a library) |
| [pytest](https://pytest.org) (tests only) | MIT |

Each component keeps its own licence. These are loaded from their official packages or CDN and are not
covered by the Skyward licence.
