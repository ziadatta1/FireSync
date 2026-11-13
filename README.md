# FireSync

FireSync is a local dashboard for viewing summaries and a clustered map of the
existing MODIS and VIIRS CSV files. Both files are labeled **UNVERIFIED LOCAL
DATA**. Their provenance has not been confirmed; the application does not claim
they are authenticated NASA observations. New FIRMS downloads are written to a
separate versioned import directory with provenance sidecars. See
[DATA_PROVENANCE.md](DATA_PROVENANCE.md).

## Requirements

- Python 3.9 or newer
- Node.js 20.19+ or 22.12+ and npm

## Run the backend

From the project root:

```sh
python3 -m venv backend/.venv
source backend/.venv/bin/activate
python -m pip install -r backend/requirements.txt
uvicorn app.main:app --app-dir backend --env-file backend/.env --reload --host 127.0.0.1 --port 8000
```

The API is available at `http://127.0.0.1:8000`. Interactive API docs are at
`http://127.0.0.1:8000/docs`.

### Optional NASA FIRMS imports

Request a free `MAP_KEY` from the [NASA FIRMS API page](https://firms.modaps.eosdis.nasa.gov/api/).
Set it only in the backend process environment; it is never read by or returned
to the frontend:

```sh
export FIRMS_MAP_KEY="<your MAP_KEY>"
```

The backend exposes `GET /api/firms/status` to report whether a non-placeholder
key is configured, `POST /api/firms/import` to fetch a selected source, and
`GET /api/firms/imports` to list stored provenance records. An import request
accepts `sensor` (`modis` or `viirs`), optional allowlisted `source`, `area`
(`world` or `west,south,east,north`), `day_range` (1–5), and optional
`acquisition_date`.
Defaults are `MODIS_NRT` and `VIIRS_NOAA21_NRT`. VIIRS also supports
`VIIRS_NOAA20_NRT` and the NOAA-20/NOAA-21 standard-processing sources.
Suomi NPP source IDs are retained for explicitly dated historical retrievals;
they are not used for live requests. Each successful, validated HTTPS CSV
response is stored under a unique `data/imports/` directory with a UTC
retrieval timestamp, source/query details, row count, columns, and SHA-256.
Existing `modis.csv` and `viirs.csv` are not overwritten. FIRMS rate limits and
provider failures are surfaced without retrying automatically.

## Run the frontend

In another terminal, from the project root:

```sh
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`. The frontend fetches the summary from the local
backend. Start both processes to see the dataset cards.

## API endpoints

- `GET /api/health` — process health status.
- `GET /api/datasets/summary` — row counts, column names, and acquisition date
  ranges for `modis.csv` and `viirs.csv`. The endpoint reads but does not modify
  the CSV files. Each dataset and the response carry the unverified status.
- `GET /api/observations/filters` — available dates, satellite codes,
  instruments, confidence values, and coordinate validation counts by dataset.
- `GET /api/observations` — filterable and paginated source rows. Supports
  repeated `dataset` values (`modis`, `viirs`), `date_from`, `date_to`,
  `satellite`, `instrument`, exact `confidence`, and an optional complete
  bounding box (`south`, `west`, `north`, `east`). Pages default to 2,000 rows
  and are capped at 5,000. Invalid-coordinate rows are counted in the response
  rather than being silently included on the map.

## Backend tests

With the backend dependencies installed, run from the project root:

```sh
pytest backend/tests
```

The frontend clusters the currently loaded page of markers and paginates each
enabled dataset so matching rows remain accessible without placing tens of
thousands of markers on the map at once. Marker clicks show source fields that
exist in that row. The basemap uses OpenStreetMap standard raster tiles and
displays OpenStreetMap attribution.

This foundation does not perform scientific harmonization, anomaly detection,
or other analysis.
