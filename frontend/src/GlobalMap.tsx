import { useCallback, useEffect, useRef, useState } from 'react'
import L from 'leaflet'
import { MapContainer, TileLayer, useMap } from 'react-leaflet'
import { DetectionLayer, type DrawItem } from './DetectionLayer.ts'
import { SOURCES, SOURCE_IDS, filterParams, fmt, getJson, type Area, type Filters, type SourceId } from './api.ts'

type GridResponse = { cell_degrees: number; layers: Partial<Record<SourceId, { cells: number[][]; detections: number }>> }
type PointsResponse = { columns: string[]; layers: Partial<Record<SourceId, { total: number; returned: number; truncated: boolean; rows: (string | number | null)[][] }>> }
type RecordResponse = { dataset: SourceId; label: string; firms_source: string; classification: string; source_row: number; satellite_name: string; confidence_class: string | null; fields: Record<string, string> }
type ViewStatus = { mode: 'cells' | 'points'; cell?: number; perSource: Partial<Record<SourceId, { shown: number; total: number; truncated?: boolean }>> }
type DrawMode = 'none' | 'rect' | 'polygon'

const POINT_ZOOM = 7
const POINT_LIMIT = 8000

function cellForZoom(zoom: number) {
  if (zoom <= 2) return 2
  if (zoom === 3) return 1
  if (zoom === 4) return 0.5
  if (zoom === 5) return 0.25
  return 0.1
}

function clampBounds(b: L.LatLngBounds) {
  const s = Math.max(-90, b.getSouth()), n = Math.min(90, b.getNorth())
  const w = Math.max(-180, b.getWest()), e = Math.min(180, b.getEast())
  return { south: s, west: w, north: n, east: e }
}

function DataLayer({ filters, onStatus, onLoading, onError, onSelect, layerRef }: {
  filters: Filters
  onStatus: (s: ViewStatus) => void
  onLoading: (v: boolean) => void
  onError: (e: string) => void
  onSelect: (items: DrawItem[], mode: 'cells' | 'points') => void
  layerRef: React.MutableRefObject<DetectionLayer | null>
}) {
  const map = useMap()
  const modeRef = useRef<'cells' | 'points'>('cells')

  useEffect(() => {
    const layer = new DetectionLayer()
    layer.addTo(map)
    layerRef.current = layer
    return () => { layer.remove(); layerRef.current = null }
  }, [map, layerRef])

  useEffect(() => {
    let controller: AbortController | null = null
    let timer: number | undefined
    const load = () => {
      controller?.abort()
      controller = new AbortController()
      const zoom = map.getZoom()
      const layer = layerRef.current
      if (!filters.sources.length) {
        layer?.setData([], 'cells')
        onStatus({ mode: 'cells', perSource: {} })
        return
      }
      const params = filterParams(filters)
      const bounds = clampBounds(map.getBounds().pad(0.25))
      onLoading(true)
      onError('')
      if (zoom >= POINT_ZOOM) {
        Object.entries(bounds).forEach(([k, v]) => params.set(k, v.toFixed(4)))
        params.set('limit', String(POINT_LIMIT))
        getJson<PointsResponse>(`/api/global/points?${params}`, controller.signal)
          .then((body) => {
            const items: DrawItem[] = []
            const perSource: ViewStatus['perSource'] = {}
            const c = body.columns
            const iLat = c.indexOf('latitude'), iLon = c.indexOf('longitude'), iFrp = c.indexOf('frp'), iRow = c.indexOf('source_row')
            for (const source of SOURCE_IDS) {
              const layerData = body.layers[source]
              if (!layerData) continue
              perSource[source] = { shown: layerData.returned, total: layerData.total, truncated: layerData.truncated }
              for (const row of layerData.rows) {
                items.push({ source, lat: row[iLat] as number, lon: row[iLon] as number, count: 1, frp: (row[iFrp] as number) ?? 0, frpMax: (row[iFrp] as number) ?? 0, sourceRow: row[iRow] as number, extra: row })
              }
            }
            modeRef.current = 'points'
            layer?.setData(items, 'points')
            onStatus({ mode: 'points', perSource })
          })
          .catch((e: unknown) => { if (!(e instanceof DOMException && e.name === 'AbortError')) onError(e instanceof Error ? e.message : 'Map data unavailable') })
          .finally(() => onLoading(false))
      } else {
        const cell = cellForZoom(zoom)
        params.set('cell', String(cell))
        if (zoom > 2) Object.entries(bounds).forEach(([k, v]) => params.set(k, v.toFixed(4)))
        getJson<GridResponse>(`/api/global/grid?${params}`, controller.signal)
          .then((body) => {
            const items: DrawItem[] = []
            const perSource: ViewStatus['perSource'] = {}
            for (const source of SOURCE_IDS) {
              const layerData = body.layers[source]
              if (!layerData) continue
              perSource[source] = { shown: layerData.cells.length, total: layerData.detections }
              for (const [lat, lon, count, frpSum, frpMax] of layerData.cells) items.push({ source, lat, lon, count, frp: frpSum, frpMax })
            }
            modeRef.current = 'cells'
            layer?.setData(items, 'cells', cell)
            onStatus({ mode: 'cells', cell, perSource })
          })
          .catch((e: unknown) => { if (!(e instanceof DOMException && e.name === 'AbortError')) onError(e instanceof Error ? e.message : 'Map data unavailable') })
          .finally(() => onLoading(false))
      }
    }
    const schedule = () => { window.clearTimeout(timer); timer = window.setTimeout(load, 220) }
    load()
    map.on('moveend', schedule)
    return () => { map.off('moveend', schedule); window.clearTimeout(timer); controller?.abort() }
  }, [map, filters, layerRef, onStatus, onLoading, onError])

  useEffect(() => {
    const click = (event: L.LeafletMouseEvent) => {
      if ((map as L.Map & { _fsDrawing?: boolean })._fsDrawing) return
      const layer = layerRef.current
      if (!layer) return
      const hit = layer.hitTest(event.containerPoint)
      if (!hit) { onSelect([], modeRef.current); layer.setHighlight(null); return }
      layer.setHighlight(hit)
      onSelect(modeRef.current === 'cells' ? layer.itemsNear(hit) : [hit], modeRef.current)
    }
    map.on('click', click)
    return () => { map.off('click', click) }
  }, [map, layerRef, onSelect])
  return null
}

function AreaTool({ mode, setMode, area, onArea }: { mode: DrawMode; setMode: (m: DrawMode) => void; area: Area | null; onArea: (a: Area | null) => void }) {
  const map = useMap()
  const shapeRef = useRef<L.Layer | null>(null)

  // Render the committed area.
  useEffect(() => {
    shapeRef.current?.remove()
    shapeRef.current = null
    if (!area) return
    const style = { color: '#c5ee79', weight: 2, fillOpacity: 0.06, dashArray: '6 4', interactive: false }
    shapeRef.current = area.kind === 'bbox'
      ? L.rectangle([[area.south, area.west], [area.north, area.east]], style).addTo(map)
      : L.polygon(area.vertices, style).addTo(map)
  }, [map, area])

  useEffect(() => {
    if (mode === 'none') return
    const m = map as L.Map & { _fsDrawing?: boolean }
    m._fsDrawing = true
    const container = map.getContainer()
    container.classList.add('drawing')
    let preview: L.Layer | null = null
    const style = { color: '#c5ee79', weight: 2, fillOpacity: 0.08, interactive: false }
    const finish = (a: Area | null) => { preview?.remove(); if (a) onArea(a); setMode('none') }

    if (mode === 'rect') {
      map.dragging.disable()
      let start: L.LatLng | null = null
      const down = (e: L.LeafletMouseEvent) => { start = e.latlng }
      const move = (e: L.LeafletMouseEvent) => {
        if (!start) return
        preview?.remove()
        preview = L.rectangle(L.latLngBounds(start, e.latlng), style).addTo(map)
      }
      const up = (e: L.LeafletMouseEvent) => {
        if (!start) return
        const b = L.latLngBounds(start, e.latlng)
        start = null
        const c = clampBounds(b)
        if (c.north - c.south < 0.05 || c.east - c.west < 0.05) { preview?.remove(); return }
        finish({ kind: 'bbox', ...c })
      }
      map.on('mousedown', down); map.on('mousemove', move); map.on('mouseup', up)
      return () => {
        map.off('mousedown', down); map.off('mousemove', move); map.off('mouseup', up)
        map.dragging.enable(); container.classList.remove('drawing'); preview?.remove()
        window.setTimeout(() => { m._fsDrawing = false }, 0)
      }
    }
    // polygon: click to add vertices, double-click (or click first vertex) to finish
    map.doubleClickZoom.disable()
    const vertices: [number, number][] = []
    const redraw = (cursor?: L.LatLng) => {
      preview?.remove()
      const pts = cursor ? [...vertices, [cursor.lat, cursor.lng] as [number, number]] : vertices
      preview = pts.length >= 2 ? L.polygon(pts, style).addTo(map) : null
    }
    const clamp = (ll: L.LatLng): [number, number] => [Math.max(-90, Math.min(90, ll.lat)), Math.max(-180, Math.min(180, ll.lng))]
    const click = (e: L.LeafletMouseEvent) => { vertices.push(clamp(e.latlng)); redraw() }
    const move = (e: L.LeafletMouseEvent) => { if (vertices.length) redraw(e.latlng) }
    const dbl = () => {
      // the dblclick also fired a click that duplicated the last vertex
      const unique = vertices.filter((v, i) => i === 0 || v[0] !== vertices[i - 1][0] || v[1] !== vertices[i - 1][1])
      finish(unique.length >= 3 ? { kind: 'polygon', vertices: unique } : null)
    }
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') finish(null); if (e.key === 'Enter') dbl() }
    map.on('click', click); map.on('mousemove', move); map.on('dblclick', dbl)
    window.addEventListener('keydown', key)
    return () => {
      map.off('click', click); map.off('mousemove', move); map.off('dblclick', dbl)
      window.removeEventListener('keydown', key)
      map.doubleClickZoom.enable(); container.classList.remove('drawing'); preview?.remove()
      window.setTimeout(() => { m._fsDrawing = false }, 0)
    }
  }, [map, mode, onArea, setMode])
  return null
}

function SizeSync({ visible }: { visible: boolean }) {
  const map = useMap()
  useEffect(() => { if (visible) window.setTimeout(() => map.invalidateSize(), 0) }, [map, visible])
  return null
}

function FlyTo({ target }: { target: { lat: number; lon: number; zoom: number; n: number } | null }) {
  const map = useMap()
  useEffect(() => { if (target) map.flyTo([target.lat, target.lon], target.zoom, { duration: 0.8 }) }, [map, target])
  return null
}

function Selection({ items, mode, onClose, onZoom }: { items: DrawItem[]; mode: 'cells' | 'points'; onClose: () => void; onZoom: (lat: number, lon: number) => void }) {
  const [record, setRecord] = useState<RecordResponse | null>(null)
  const [error, setError] = useState('')
  const first = items[0]
  useEffect(() => {
    setRecord(null); setError('')
    if (mode !== 'points' || !first?.sourceRow) return
    const controller = new AbortController()
    getJson<RecordResponse>(`/api/global/record/${first.source}/${first.sourceRow}`, controller.signal)
      .then(setRecord)
      .catch((e: unknown) => { if (!(e instanceof DOMException)) setError(e instanceof Error ? e.message : 'Record unavailable') })
    return () => controller.abort()
  }, [first, mode])
  if (!first) return null
  return (
    <aside className="observation-details global-details" aria-label="Selection details">
      <div className="details-heading">
        <div><span className="map-kicker">{mode === 'cells' ? 'GRID CELL (AGGREGATE)' : 'SINGLE DETECTION'}</span><h3>{first.lat.toFixed(mode === 'cells' ? 2 : 5)}°, {first.lon.toFixed(mode === 'cells' ? 2 : 5)}°</h3></div>
        <button type="button" onClick={onClose} aria-label="Close details">×</button>
      </div>
      {mode === 'cells' ? (
        <>
          <p className="details-note">Cell centre shown. Counts are detections (fire pixels) in this display cell for the active filters — not distinct fires.</p>
          <dl>
            {items.map((item) => (
              <div className="detail-row" key={item.source}><dt><i className="swatch" style={{ background: SOURCES[item.source].color }} />{SOURCES[item.source].short}</dt><dd>{fmt(item.count)} det · ΣFRP {fmt(item.frp, 1)} MW · max {fmt(item.frpMax, 1)}</dd></div>
            ))}
          </dl>
          <button type="button" className="ghost-button" onClick={() => onZoom(first.lat, first.lon)}>Zoom to cell →</button>
        </>
      ) : (
        <>
          <div className="detail-provenance"><i className="swatch" style={{ background: SOURCES[first.source].color }} />{SOURCES[first.source].label}</div>
          {error && <div className="error-panel">{error}</div>}
          {!record && !error && <p className="details-note">Loading source record…</p>}
          {record && (
            <>
              <div className="map-kicker">{record.firms_source} · ROW {record.source_row.toLocaleString()} · {record.classification}</div>
              <dl>
                {([
                  ['Latitude', record.fields.latitude], ['Longitude', record.fields.longitude],
                  ['Acquired (UTC)', `${record.fields.acq_date} ${record.fields.acq_time.padStart(4, '0').replace(/(\d\d)(\d\d)/, '$1:$2')}`],
                  ['Satellite', `${record.satellite_name} (${record.fields.satellite})`],
                  ['Confidence', `${record.fields.confidence}${record.confidence_class ? ` · ${record.confidence_class}` : ''}`],
                  ['FRP', `${record.fields.frp} MW`],
                  [record.fields.bright_ti4 !== undefined ? 'Bright. I4' : 'Bright. T21', `${record.fields.bright_ti4 ?? record.fields.brightness} K`],
                  [record.fields.bright_ti5 !== undefined ? 'Bright. I5' : 'Bright. T31', `${record.fields.bright_ti5 ?? record.fields.bright_t31} K`],
                  ['Scan × track', `${record.fields.scan} × ${record.fields.track} km`],
                  ['Day / night', record.fields.daynight === 'D' ? 'Day' : 'Night'],
                  ['Version', record.fields.version],
                ] as [string, string][]).map(([label, value]) => <div className="detail-row" key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
              </dl>
            </>
          )}
        </>
      )}
    </aside>
  )
}

export default function GlobalMap({ filters, area, onArea, focus, visible = true }: {
  filters: Filters
  area: Area | null
  onArea: (a: Area | null) => void
  focus: { lat: number; lon: number; zoom: number; n: number } | null
  visible?: boolean
}) {
  const layerRef = useRef<DetectionLayer | null>(null)
  const [status, setStatus] = useState<ViewStatus>({ mode: 'cells', perSource: {} })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [selection, setSelection] = useState<{ items: DrawItem[]; mode: 'cells' | 'points' }>({ items: [], mode: 'cells' })
  const [drawMode, setDrawMode] = useState<DrawMode>('none')
  const [flyTarget, setFlyTarget] = useState<{ lat: number; lon: number; zoom: number; n: number } | null>(null)

  useEffect(() => { if (focus) setFlyTarget(focus) }, [focus])
  const onSelect = useCallback((items: DrawItem[], mode: 'cells' | 'points') => setSelection({ items, mode }), [])
  const closeSelection = () => { setSelection({ items: [], mode: 'cells' }); layerRef.current?.setHighlight(null) }

  const totalShown = Object.values(status.perSource).reduce((s, v) => s + (v?.total ?? 0), 0)
  const truncated = Object.values(status.perSource).some((v) => v?.truncated)

  return (
    <div className="global-map-wrap">
      <div className="map-statusbar">
        <span>{loading ? 'LOADING…' : status.mode === 'cells' ? `${status.cell}° CELLS · ${fmt(totalShown)} DETECTIONS IN VIEW` : `INDIVIDUAL DETECTIONS · ${fmt(totalShown)} IN VIEW`}</span>
        <span className="status-sources">
          {SOURCE_IDS.filter((s) => status.perSource[s]).map((s) => (
            <span key={s}><i className={`shape-key ${SOURCES[s].shape}`} style={{ background: SOURCES[s].color }} />{SOURCES[s].short} {fmt(status.perSource[s]!.total)}</span>
          ))}
        </span>
      </div>
      <div className="leaflet-frame global-frame">
        <MapContainer center={[15, 10]} zoom={2} minZoom={2} maxZoom={14} scrollWheelZoom className="firesync-map global" maxBounds={[[-85, -200], [85, 200]]} maxBoundsViscosity={0.8} preferCanvas>
          <TileLayer
            attribution='Basemap &copy; Esri, HERE, Garmin, &copy; OpenStreetMap contributors'
            url="https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}"
            maxNativeZoom={16}
            noWrap
          />
          <DataLayer filters={filters} onStatus={setStatus} onLoading={setLoading} onError={setError} onSelect={onSelect} layerRef={layerRef} />
          <AreaTool mode={drawMode} setMode={setDrawMode} area={area} onArea={onArea} />
          <FlyTo target={flyTarget} />
          <SizeSync visible={visible} />
        </MapContainer>
        <div className="map-tools" role="toolbar" aria-label="Area of interest tools">
          <span className="map-kicker">AREA</span>
          <button type="button" className={drawMode === 'rect' ? 'active' : ''} onClick={() => setDrawMode(drawMode === 'rect' ? 'none' : 'rect')} title="Drag on the map to draw a rectangle">▭ Box</button>
          <button type="button" className={drawMode === 'polygon' ? 'active' : ''} onClick={() => setDrawMode(drawMode === 'polygon' ? 'none' : 'polygon')} title="Click vertices, double-click or Enter to finish, Esc to cancel">⬠ Polygon</button>
          <button type="button" onClick={() => onArea(null)} disabled={!area}>Clear</button>
        </div>
        {drawMode !== 'none' && <div className="draw-hint">{drawMode === 'rect' ? 'Drag to draw a box.' : 'Click to add vertices · double-click or Enter to finish · Esc to cancel.'}</div>}
        {selection.items.length > 0 && <Selection items={selection.items} mode={selection.mode} onClose={closeSelection} onZoom={(lat, lon) => setFlyTarget({ lat, lon, zoom: 8, n: Date.now() })} />}
        {!filters.sources.length && <div className="map-empty-overlay">Enable at least one satellite source.</div>}
        {!loading && !error && filters.sources.length > 0 && totalShown === 0 && <div className="map-empty-overlay">No detections match these filters in this view. (No detection ≠ no fire: clouds, overpass timing and detection limits apply.)</div>}
        {error && <div className="map-empty-overlay error">Map data unavailable: {error}</div>}
      </div>
      <div className="map-footer">
        <div className="map-legend">
          {SOURCE_IDS.map((s) => <span key={s}><i className={`shape-key ${SOURCES[s].shape}`} style={{ background: SOURCES[s].color }} />{SOURCES[s].label}</span>)}
        </div>
        <span className="legend-note">
          {status.mode === 'cells'
            ? `Zoom ≤ 6: detections aggregated server-side into ${status.cell}° cells (mark size ∝ log count). Zoom ≥ ${POINT_ZOOM}: individual detections.`
            : truncated ? `More than ${fmt(POINT_LIMIT)} detections in view for a source — showing the highest-FRP ${fmt(POINT_LIMIT)}; zoom in for all.` : 'Every matching detection in view is drawn. Click one for its full source record.'}
        </span>
      </div>
    </div>
  )
}
