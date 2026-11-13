import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import L from 'leaflet'
import 'leaflet.markercluster'
import { MapContainer, TileLayer, useMap } from 'react-leaflet'

const API = import.meta.env.VITE_API_URL ?? 'http://127.0.0.1:8000'
const PAGE_SIZE = 1000
type Sensor = 'modis' | 'viirs'
type Observation = Record<string, string | number> & {
  dataset: Sensor
  latitude: number
  longitude: number
  source_row: number
  import_id?: string
  firms_source?: string
  provenance_status?: string
}
type DatasetFilters = {
  columns: string[]
  dates: string[]
  satellites: string[]
  instruments: string[]
  confidence_values: string[]
  invalid_coordinate_count: number
}
type FiltersResponse = { data_status: string; datasets: Record<Sensor, DatasetFilters> }
type ObservationPage = {
  data_status: string
  total: number
  has_more: boolean
  invalid_coordinate_count: number
  empty_message?: string | null
  observations: Observation[]
  provenance?: ImportRecord
  filters?: DatasetFilters
}
type ImportRecord = {
  import_id: string
  sensor: Sensor
  source: string
  classification: string
  retrieved_at: string
  record_count: number
  sha256: string
  query?: { area?: string; day_range?: number; date?: string | null }
}
type AppliedFilters = { dateFrom: string; dateTo: string; satellite: string; instrument: string; confidence: string }

function markerIcon(sensor: Sensor) {
  return L.divIcon({
    className: 'observation-marker-host',
    html: `<span class="observation-marker ${sensor}" aria-hidden="true"></span>`,
    iconSize: [18, 18],
    iconAnchor: [9, 9],
  })
}

function ClusteredObservations({
  observations,
  onSelect,
}: {
  observations: Observation[]
  onSelect: (observation: Observation) => void
}) {
  const map = useMap()

  useEffect(() => {
    const group = L.markerClusterGroup({
      chunkedLoading: true,
      chunkInterval: 100,
      chunkDelay: 25,
      maxClusterRadius: 52,
      spiderfyOnMaxZoom: true,
      showCoverageOnHover: false,
      animate: false,
    })
    const markers = observations.map((observation) => {
      const marker = L.marker([observation.latitude, observation.longitude], {
        icon: markerIcon(observation.dataset),
        title: `${observation.dataset.toUpperCase()} observation`,
        keyboard: true,
      })
      marker.on('click', () => onSelect(observation))
      return marker
    })
    group.addLayers(markers)
    map.addLayer(group)
    return () => {
      map.removeLayer(group)
      group.clearLayers()
    }
  }, [map, observations, onSelect])

  return null
}

function ObservationDetails({ observation, onClose }: { observation: Observation; onClose: () => void }) {
  const fields: [string, string][] = []
  const add = (key: string, label: string, format?: (value: string | number) => string) => {
    const value = observation[key]
    if (value !== undefined && value !== '') fields.push([label, format ? format(value) : String(value)])
  }
  add('latitude', 'Latitude', (value) => Number(value).toFixed(5))
  add('longitude', 'Longitude', (value) => Number(value).toFixed(5))
  add('acq_date', 'Acquisition date')
  add('acq_time', 'Acquisition time')
  if ('bright_ti4' in observation) add('bright_ti4', 'Brightness (T4)')
  else add('brightness', 'Brightness')
  add('confidence', 'Confidence')
  add('frp', 'Fire radiative power')
  add('satellite', 'Satellite')
  add('instrument', 'Instrument')

  return (
    <aside className="observation-details" aria-label="Selected observation details">
      <div className="details-heading"><div><span className="map-kicker">SELECTED RECORD</span><h3>{observation.dataset.toUpperCase()}</h3></div><button type="button" onClick={onClose} aria-label="Close observation details">×</button></div>
      <div className="detail-provenance">{observation.provenance_status ?? 'UNVERIFIED LOCAL DATA'}</div>
      {observation.firms_source && <div className="map-kicker">FIRMS SOURCE · {observation.firms_source}</div>}
      <dl>{fields.map(([label, value]) => <div className="detail-row" key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    </aside>
  )
}

export default function WildfireMap() {
  const [dataMode, setDataMode] = useState<'local' | 'imports'>('local')
  const [metadata, setMetadata] = useState<FiltersResponse | null>(null)
  const [metaError, setMetaError] = useState('')
  const [enabled, setEnabled] = useState<Record<Sensor, boolean>>({ modis: true, viirs: true })
  const activeSensors = useMemo(() => (Object.keys(enabled) as Sensor[]).filter((sensor) => enabled[sensor]), [enabled])
  const [draft, setDraft] = useState<AppliedFilters>({ dateFrom: '', dateTo: '', satellite: '', instrument: '', confidence: '' })
  const [filters, setFilters] = useState<AppliedFilters>({ dateFrom: '', dateTo: '', satellite: '', instrument: '', confidence: '' })
  const [offset, setOffset] = useState(0)
  const [observations, setObservations] = useState<Observation[]>([])
  const [totals, setTotals] = useState<Partial<Record<Sensor, number>>>({})
  const [hasMore, setHasMore] = useState(false)
  const [invalidCoordinates, setInvalidCoordinates] = useState(0)
  const [selected, setSelected] = useState<Observation | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [imports, setImports] = useState<ImportRecord[]>([])
  const [importsError, setImportsError] = useState('')
  const [selectedSource, setSelectedSource] = useState('MODIS')
  const [selectedImportId, setSelectedImportId] = useState('')
  const [importPage, setImportPage] = useState<ObservationPage | null>(null)

  useEffect(() => {
    fetch(`${API}/api/observations/filters`)
      .then((response) => {
        if (!response.ok) throw new Error(`API returned ${response.status}`)
        return response.json() as Promise<FiltersResponse>
      })
      .then(setMetadata)
      .catch((reason: unknown) => setMetaError(reason instanceof Error ? reason.message : 'Could not load filter options'))
  }, [])

  useEffect(() => {
    let cancelled = false
    fetch(`${API}/api/firms/imports`)
      .then((response) => {
        if (!response.ok) throw new Error(`API returned ${response.status}`)
        return response.json() as Promise<{ imports: ImportRecord[] }>
      })
      .then((body) => { if (!cancelled) { setImports(body.imports); setImportsError('') } })
      .catch((reason: unknown) => { if (!cancelled) setImportsError(reason instanceof Error ? reason.message : 'Could not load imported datasets') })
    return () => { cancelled = true }
  }, [])

  const matchingImports = useMemo(() => imports.filter((item) => selectedSource === 'MODIS'
    ? item.source.startsWith('MODIS_')
    : item.source.startsWith(`${selectedSource}_`)), [imports, selectedSource])
  const availableImport = matchingImports.find((item) => item.import_id === selectedImportId) ?? matchingImports[0]

  useEffect(() => {
    if (availableImport && availableImport.import_id !== selectedImportId) setSelectedImportId(availableImport.import_id)
  }, [availableImport, selectedImportId])

  useEffect(() => {
    let cancelled = false
    if (dataMode === 'imports') {
      setObservations([])
      setTotals({})
      setHasMore(false)
      setInvalidCoordinates(0)
      setSelected(null)
      setImportPage(null)
      setError('')
      if (!availableImport) {
        setLoading(false)
        return () => { cancelled = true }
      }
      setLoading(true)
      const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) })
      if (filters.dateFrom) params.set('date_from', filters.dateFrom)
      if (filters.dateTo) params.set('date_to', filters.dateTo)
      if (filters.satellite) params.set('satellite', filters.satellite)
      if (filters.instrument) params.set('instrument', filters.instrument)
      if (filters.confidence) params.set('confidence', filters.confidence)
      fetch(`${API}/api/firms/imports/${encodeURIComponent(availableImport.import_id)}/observations?${params}`)
        .then((response) => { if (!response.ok) throw new Error(`API returned ${response.status}`); return response.json() as Promise<ObservationPage> })
        .then((page) => {
          if (cancelled) return
          setImportPage(page)
          setObservations(page.observations)
          setTotals({ [availableImport.sensor]: page.total })
          setHasMore(page.has_more)
          setInvalidCoordinates(page.invalid_coordinate_count ?? 0)
        })
        .catch((reason: unknown) => { if (!cancelled) setError(reason instanceof Error ? reason.message : 'Could not load imported observations') })
        .finally(() => { if (!cancelled) setLoading(false) })
      return () => { cancelled = true }
    }
    if (activeSensors.length === 0) {
      setObservations([])
      setTotals({})
      setHasMore(false)
      setInvalidCoordinates(0)
      setSelected(null)
      return
    }
    setObservations([])
    setTotals({})
    setSelected(null)
    setLoading(true)
    setError('')
    const load = async () => {
      const pages = await Promise.all(activeSensors.map(async (sensor) => {
        const params = new URLSearchParams({ dataset: sensor, limit: String(PAGE_SIZE), offset: String(offset) })
        if (filters.dateFrom) params.set('date_from', filters.dateFrom)
        if (filters.dateTo) params.set('date_to', filters.dateTo)
        if (filters.satellite) params.set('satellite', filters.satellite)
        if (filters.instrument) params.set('instrument', filters.instrument)
        if (filters.confidence) params.set('confidence', filters.confidence)
        const response = await fetch(`${API}/api/observations?${params}`)
        if (!response.ok) throw new Error(`API returned ${response.status}`)
        return [sensor, await response.json() as ObservationPage] as const
      }))
      if (cancelled) return
      const nextTotals: Partial<Record<Sensor, number>> = {}
      const nextRows: Observation[] = []
      let nextInvalidCoordinates = 0
      let nextHasMore = false
      for (const [sensor, page] of pages) {
        nextTotals[sensor] = page.total
        nextRows.push(...page.observations)
        nextInvalidCoordinates += page.invalid_coordinate_count
        nextHasMore ||= page.has_more
      }
      setObservations(nextRows)
      setTotals(nextTotals)
      setInvalidCoordinates(nextInvalidCoordinates)
      setHasMore(nextHasMore)
      setSelected(null)
    }
    load()
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : 'Could not load observations')
      })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [dataMode, availableImport, activeSensors, filters, offset])

  const selectObservation = useCallback((observation: Observation) => setSelected(observation), [])
  const submitFilters = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setOffset(0)
    setFilters({ ...draft })
  }
  const resetFilters = () => {
    const empty = { dateFrom: '', dateTo: '', satellite: '', instrument: '', confidence: '' }
    setDraft(empty)
    setFilters(empty)
    setEnabled({ modis: true, viirs: true })
    setOffset(0)
  }
  const toggleSensor = (sensor: Sensor) => {
    setEnabled((current) => ({ ...current, [sensor]: !current[sensor] }))
    setOffset(0)
  }
  const sensorMetadata = activeSensors.flatMap((sensor) => metadata?.datasets[sensor] ? [metadata.datasets[sensor]] : [])
  const satellites = [...new Set(sensorMetadata.flatMap((item) => item.satellites))].sort()
  const instruments = [...new Set(sensorMetadata.flatMap((item) => item.instruments))].sort()
  const totalMatching = activeSensors.reduce((sum, sensor) => sum + (totals[sensor] ?? 0), 0)
  const activeFilterMetadata = dataMode === 'imports' ? importPage?.filters : sensorMetadata.length ? {
    columns: [], dates: [], satellites, instruments,
    confidence_values: sensorMetadata.flatMap((item) => item.confidence_values), invalid_coordinate_count: invalidCoordinates,
  } : undefined
  const shownSatellites = dataMode === 'imports' ? importPage?.filters?.satellites ?? [] : satellites
  const shownInstruments = dataMode === 'imports' ? importPage?.filters?.instruments ?? [] : instruments
  const shownConfidence = dataMode === 'imports' ? importPage?.filters?.confidence_values ?? [] : activeFilterMetadata?.confidence_values ?? []
  const currentStatus = dataMode === 'imports' ? importPage?.data_status ?? (availableImport?.classification ?? 'VERIFIED NASA FIRMS DATA') : metadata?.data_status ?? 'UNVERIFIED LOCAL DATA'

  return (
    <section className="map-section" aria-labelledby="map-title">
      <div className="section-heading map-section-heading">
        <div><span className="section-kicker">03 / GEOSPATIAL VIEW</span><h2 id="map-title">Observation map</h2></div>
        <span className="catalog-meta">{dataMode === 'imports' ? currentStatus : 'UNVERIFIED LOCAL DATA'} <span className="mini-pulse" /></span>
      </div>

      <form className="map-controls" onSubmit={submitFilters}>
        <div className="layer-toggles" aria-label="Observation source">
          <span className="control-label">DATA SOURCE</span>
          <button className={`layer-toggle ${dataMode === 'local' ? 'checked' : ''}`} type="button" onClick={() => { setDataMode('local'); setOffset(0) }}>Local files</button>
          <button className={`layer-toggle ${dataMode === 'imports' ? 'checked' : ''}`} type="button" onClick={() => { setDataMode('imports'); setOffset(0) }}>NASA FIRMS imports</button>
        </div>
        {dataMode === 'local' && <div className="layer-toggles" aria-label="Dataset layers">
          <span className="control-label">LAYERS</span>
          <label className={`layer-toggle modis-toggle ${enabled.modis ? 'checked' : ''}`}><input type="checkbox" checked={enabled.modis} onChange={() => toggleSensor('modis')} /><span className="layer-symbol modis-symbol" />MODIS</label>
          <label className={`layer-toggle viirs-toggle ${enabled.viirs ? 'checked' : ''}`}><input type="checkbox" checked={enabled.viirs} onChange={() => toggleSensor('viirs')} /><span className="layer-symbol viirs-symbol" />VIIRS</label>
        </div>}
        {dataMode === 'imports' && <div className="filter-fields import-selectors">
          <label><span>FIRMS SOURCE</span><select value={selectedSource} onChange={(event) => { setSelectedSource(event.target.value); setSelectedImportId(''); setOffset(0); setSelected(null) }}><option value="MODIS">MODIS</option><option value="VIIRS_NOAA20">VIIRS NOAA-20</option><option value="VIIRS_NOAA21">VIIRS NOAA-21</option></select></label>
          <label><span>IMPORTED SNAPSHOT</span><select value={availableImport?.import_id ?? ''} onChange={(event) => { setSelectedImportId(event.target.value); setOffset(0) }} disabled={!matchingImports.length}><option value="">{matchingImports.length ? 'Select import' : 'No import available'}</option>{matchingImports.map((item) => <option key={item.import_id} value={item.import_id}>{item.retrieved_at} · {item.record_count.toLocaleString()} rows</option>)}</select></label>
        </div>}
        <div className="filter-fields">
          <label><span>FROM DATE</span><input type="date" value={draft.dateFrom} onChange={(event) => setDraft({ ...draft, dateFrom: event.target.value })} /></label>
          <label><span>TO DATE</span><input type="date" value={draft.dateTo} onChange={(event) => setDraft({ ...draft, dateTo: event.target.value })} /></label>
          <label><span>SATELLITE</span><select value={draft.satellite} onChange={(event) => setDraft({ ...draft, satellite: event.target.value })}><option value="">All</option>{shownSatellites.map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
          <label><span>INSTRUMENT</span><select value={draft.instrument} onChange={(event) => setDraft({ ...draft, instrument: event.target.value })}><option value="">All</option>{shownInstruments.map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
          <label><span>CONFIDENCE VALUE</span><input value={draft.confidence} onChange={(event) => setDraft({ ...draft, confidence: event.target.value.trim() })} placeholder="e.g. h, 82" list="confidence-options" /><datalist id="confidence-options">{shownConfidence.filter((value, index, values) => values.indexOf(value) === index).slice(0, 30).map((value) => <option key={value} value={value} />)}</datalist></label>
        </div>
        <div className="filter-actions"><button className="apply-filters" type="submit">Apply filters</button><button className="reset-filters" type="button" onClick={resetFilters}>Reset</button></div>
      </form>

      {metaError && <div className="error-panel">Filter options unavailable: {metaError}</div>}
      {dataMode === 'imports' && importsError && <div className="error-panel">Imported dataset catalog unavailable: {importsError}</div>}
      {error && <div className="error-panel">Observation feed unavailable: {error}. Start the backend to load the map.</div>}
      {invalidCoordinates > 0 && <div className="map-warning">{invalidCoordinates.toLocaleString()} source rows have invalid coordinates and cannot be placed on the map. They are counted in the API response and are not silently omitted.</div>}

      <div className="map-statusbar">
        <span>{loading ? 'LOADING OBSERVATIONS…' : `PAGE ${Math.floor(offset / PAGE_SIZE) + 1} · ${observations.length.toLocaleString()} ROWS OF ${totalMatching.toLocaleString()} MATCHING`}</span>
        <span>{observations.length.toLocaleString()} markers · clustered</span>
      </div>
      <div className="map-layout">
        <div className="leaflet-frame">
          <MapContainer center={[20, 5]} zoom={2} minZoom={2} maxZoom={18} scrollWheelZoom className="firesync-map" worldCopyJump>
            <TileLayer
              attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
              url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
            />
            <ClusteredObservations observations={observations} onSelect={selectObservation} />
          </MapContainer>
          {selected && <ObservationDetails observation={selected} onClose={() => setSelected(null)} />}
          {dataMode === 'local' && activeSensors.length === 0 && <div className="map-empty-overlay">Enable a data layer to show observations.</div>}
          {dataMode === 'imports' && !loading && !error && imports.length === 0 && <div className="map-empty-overlay">No NASA FIRMS imports are available yet.</div>}
          {dataMode === 'imports' && !loading && !error && imports.length > 0 && !matchingImports.length && <div className="map-empty-overlay">No imported dataset is available for this FIRMS source.</div>}
          {!loading && !error && observations.length === 0 && ((dataMode === 'local' && activeSensors.length > 0) || (dataMode === 'imports' && !!availableImport)) && <div className="map-empty-overlay">{dataMode === 'imports' && importPage?.empty_message ? importPage.empty_message : 'No observations match the selected filters.'}</div>}
        </div>
      </div>
      <div className="map-footer">
        <div className="map-legend"><span><i className="legend-dot modis-dot" /> MODIS</span><span><i className="legend-dot viirs-dot" /> VIIRS</span><span className="legend-note">Cluster markers combine nearby rows at this zoom; zoom in to inspect individual records.</span></div>
        <div className="page-actions"><button type="button" onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))} disabled={offset === 0 || loading}>← Previous</button><span>Page {Math.floor(offset / PAGE_SIZE) + 1}</span><button type="button" onClick={() => setOffset(offset + PAGE_SIZE)} disabled={!hasMore || loading}>Next →</button></div>
      </div>
      <p className="map-source-note">{PAGE_SIZE.toLocaleString()} rows per dataset are loaded at a time. Use pagination to reach every matching source row. {dataMode === 'imports' ? `${currentStatus}${importPage?.provenance ? ` · ${importPage.provenance.source} · Retrieved ${importPage.provenance.retrieved_at} · SHA-256 ${importPage.provenance.sha256}` : ''}` : `${metadata?.data_status ?? 'UNVERIFIED LOCAL DATA'} · Source provenance has not been confirmed.`}</p>
    </section>
  )
}
