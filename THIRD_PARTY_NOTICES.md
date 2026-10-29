# Third-party notices

## Included in this repository

### Leaflet 1.9.4 (`web/vendor/leaflet/`)

The map library. Copied unmodified from the npm package `leaflet@1.9.4`
(<https://registry.npmjs.org/leaflet/-/leaflet-1.9.4.tgz>, integrity
`sha512-nxS1ynzJOmOlHp+iL3FyWqK89GtNL8U8rvlMOsQdTTssxZwCXh8N2NB3GDQOL+YR3XnWyZAxwQixURb+FA74PA==`):
`dist/leaflet-src.esm.js`, `dist/leaflet.css`, `dist/images/*.png` and `LICENSE`.

Copyright (c) 2010-2023, Volodymyr Agafonkin; Copyright (c) 2010-2011, CloudMade.
BSD 2-Clause License; the full text is in `web/vendor/leaflet/LICENSE`.

## Fetched while running

### OpenStreetMap map tiles

The map loads its background from the tile server set as `[map] tile_url`, by default
<https://tile.openstreetmap.org>; no tiles are stored in this repository.

- Map data © OpenStreetMap contributors, under the Open Database License (ODbL)
  1.0: <https://www.openstreetmap.org/copyright>. The map shows this attribution in its
  corner, and it must stay visible.
- The tile servers' usage policy applies: <https://operations.osmfoundation.org/policies/tiles/>.
  One station browsing is fine; bulk downloading and offline use are not. Offline use
  needs a tile server of your own.

## Installed by pip (not part of this repository)

What the station needs to run, with the versions pinned in `pyproject.toml`:

| Package | License |
| --- | --- |
| pymavlink 2.4.50 | LGPL-3.0 |
| fastapi 0.162.0 | MIT |
| starlette 1.10.0 | BSD-3-Clause |
| uvicorn 0.54.0 | BSD-3-Clause |
| websockets 17.3 | BSD-3-Clause |

Pulled in by those: annotated-doc, annotated-types, anyio, fastcrc, h11, pydantic,
pydantic-core and typing-inspection (MIT); click, idna and lxml (BSD-3-Clause);
opentelemetry-api (Apache-2.0); typing-extensions (PSF-2.0).

pymavlink is used here as an unmodified library that pip installs. A package that
ships pymavlink itself, such as an app bundled with its dependencies, must follow
the LGPL-3.0 for it: include its license text, offer its source, and let the user
replace it with another version.
