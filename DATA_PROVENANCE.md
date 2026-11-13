# FireSync data provenance

## Global datasets (primary data, registered 2026-11-13)

FireSync's main map, analytics and sensor comparison use three NASA FIRMS global 7-day
active-fire CSV files, supplied as downloads. Each is registered by
`backend/scripts/register_global.py`. The script copies the file byte-for-byte to
`data/global/<dataset>/observations.csv` (the originals in `~/Downloads` are untouched) and
writes a `metadata.json` sidecar. The datasets are never merged or modified, and
re-registering a different file under the same name is refused.

| Dataset | Original file | FIRMS source | Rows | UTC dates | Satellite codes | SHA-256 |
|---|---|---|---:|---|---|---|
| `modis` | `MODIS_C6_1_Global_7d.csv` | `MODIS_NRT` (6.1NRT) | 81,258 | 2026-11-06 → 2026-11-13 (partial) | A 51,721 · T 29,537 | `e156a5e6f37fa5bc6b4efd0797bd46fc1901f2d9a168477a3885045bc8141ea8` |
| `noaa20` | `J1_VIIRS_C2_Global_7d.csv` | `VIIRS_NOAA20_NRT` (2.0NRT) | 416,571 | 2026-11-06 → 2026-11-13 (partial) | N20 | `2b1bc0c51043756043dec29ce9b1c8783c6fffde5f5fc78c64ea2b4451de7a33` |
| `noaa21` | `J2_VIIRS_C2_Global_7d.csv` | `VIIRS_NOAA21_NRT` (2.0NRT) | 422,644 | 2026-11-06 → 2026-11-13 (partial) | N21 | `ce4d7230a0b16265bf6967d1913db7dbba90ee8a44429b0da18edd6c2d70ef5a` |

Validation applied to every row: required FIRMS columns, numeric in-range coordinates, ISO
dates, numeric FRP, and expected satellite codes only. All three files have 0 duplicate
rows, 0 invalid rows, and 0 missing values. Extents run from about 53°S to 72°N across all
longitudes. The application re-checks the SHA-256 and row count on every load and returns
an error rather than serving altered data.

### Authenticity cross-check

The downloads were not fetched by FireSync itself. They were checked against rows that
FireSync retrieved **directly from the FIRMS API** with the backend MAP_KEY (stored under
`data/imports/`, with the world area and a 1-day range). A row matches when latitude,
longitude, acquisition date and time are identical.

| Dataset | API import (retrieved) | API rows in granules present in the download | Matched exactly |
|---|---|---:|---:|
| `modis` | `MODIS_NRT` 2026-11-13T11:56:50Z | 1,923 | **1,923 (100%)** |
| `noaa20` | `VIIRS_NOAA20_NRT` 2026-11-13T19:03:18Z | 12,709 | **12,709 (100%)** |
| `noaa21` | `VIIRS_NOAA21_NRT` 2026-11-13T12:02:38Z | 13,749 | **13,749 (100%)** |

API rows from granules (acquisition date and time) absent from the download are excluded
from the denominator: 143 for NOAA-21 and 20,016 for NOAA-20. Those are NRT granules that
arrived after the download snapshot was taken. For NOAA-21, 18 matched rows differ by
0.01 MW in FRP, which is a rounding difference between the API and file outputs.

The three datasets are therefore classified **NASA FIRMS DOWNLOAD (CROSS-CHECKED)**: genuine
FIRMS output, independently confirmed against the API, but not retrieved by the importer.

Additional observation: every one of the 14,776 rows in the legacy local `modis.csv` appears
identically (7 fields) in the cross-checked MODIS global file. It covers all of 2026-11-11
and part of 2026-11-12, so `modis.csv` is very likely an earlier FIRMS MODIS NRT snapshot.
It is still labelled unverified in the legacy view, because its own download record is
missing.

## Legacy local files and API imports (original foundation)

### Status

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
- **NASA FIRMS DOWNLOAD (CROSS-CHECKED)** — a user-supplied FIRMS file whose
  rows exactly match an independent FIRMS API retrieval for every shared
  granule (see the global datasets above).
- **NASA FIRMS DOWNLOAD (USER-SUPPLIED)** — a FIRMS-format download that has
  not (yet) been cross-checked. No current dataset remains in this class.

Additional API imports on 2026-11-13: MODIS_NRT world 1-day (1,923 rows),
VIIRS_NOAA21_NRT world 1-day (13,892 rows), and VIIRS_NOAA20_NRT world 1-day
(32,725 rows). These are used for the cross-checks above.

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
