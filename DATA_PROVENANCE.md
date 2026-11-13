# FireSync data provenance

## Status

The provenance and intended role of the CSV files in this workspace have not
been confirmed. Their filenames and column names are consistent with common
NASA FIRMS MODIS and VIIRS active-fire exports, but that alone does not verify
their source, completeness, or authenticity. Until source records are supplied,
do not describe these files as verified NASA observations or use them to make
scientific or operational claims. They are not confirmed demonstration data
either; treat them as **unverified local data**.

The project includes a backend NASA FIRMS API import path. A MAP_KEY is
configured locally in `backend/.env`; its value is not recorded here. On
2026-11-12, one MODIS and one Suomi NPP VIIRS test request each returned HTTP
200 with valid CSV headers and zero observation rows. Their imported files and
provenance sidecars are retained under `data/imports/`; these are empty results,
not fire observations. The original CSV files remain unverified.

NASA FIRMS announced that Suomi NPP delivery ceased on November 2, 2026. New
live VIIRS requests now default to NOAA-21 (`VIIRS_NOAA21_NRT`); NOAA-20
(`VIIRS_NOAA20_NRT`) is also supported. Suomi NPP source IDs remain available
only for explicitly dated historical retrievals. Source identifiers follow
[NASA FIRMS Area API documentation](https://firms.modaps.eosdis.nasa.gov/api/area/)
and the [FIRMS Suomi NPP announcement](https://firms.modaps.eosdis.nasa.gov/content/notifications/firms/news.html).

An import is classified as verified only after a successful HTTPS FIRMS
response passes CSV schema and coordinate validation; each import is stored
separately with retrieval time, requested source and parameters, row count,
columns, and file hash.

## Provenance classes

- **VERIFIED NASA FIRMS DATA** — reserved for a successful, validated response
  from the official NASA FIRMS API. A configured key alone is not evidence.
- **UNVERIFIED LOCAL DATA** — the existing `modis.csv` and `viirs.csv`; their
  source records have not been confirmed.
- **DEMONSTRATION DATA** — reserved for data explicitly generated or labeled
  for demonstration. No current dataset in this project is classified this way.

## Local files inspected

| File | Rows | Fields | Acquisition dates in file | What can be established |
| --- | ---: | --- | --- | --- |
| `viirs.csv` | 74,605 | 14, including `instrument`, `version`, `confidence`, and `frp` | 2023-07-12 only | CSV parses successfully; its field names and values are consistent with VIIRS active-fire records. Source and intended use remain unconfirmed. |
| `modis.csv` | 14,776 | 13, including `version`, `confidence`, and `frp` | 2026-11-11 through 2026-11-12 | CSV parses successfully; its field names and values are consistent with MODIS active-fire records. Source and intended use remain unconfirmed. |

Both files had zero missing values in a pandas null-count check. This is only a
structural observation, not a validation of the records or their origin.

## Integrity identifiers

SHA-256 hashes identify the exact files inspected on 2026-11-12; they do not
establish provenance:

- `viirs.csv`: `edf79f62b35ba4485d7c85e01be5f6f61d30ab7b4addcf2eea00e2581982c30f`
- `modis.csv`: `d29ac4a468594a1af88c4318d3cd4354e9a9b5b43b97cbec699ef6035105ff85`

## Information needed to resolve provenance

For each file, record:

1. The original download URL or NASA FIRMS product page and product identifier.
2. Download date and time, date range, geographic area, and any filters used.
3. Whether the file is an unmodified source export, a transformed extract, or
   synthetic/example data; if transformed, record the steps and retain the
   original export where possible.
4. The intended project use and any applicable source attribution or license
   requirements.

Until those details are confirmed, keep the files unchanged and label any
interface or report using them as **unverified local data**. Do not imply that
they are live, current, complete, or verified NASA observations.
