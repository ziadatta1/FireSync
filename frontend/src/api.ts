export const API = import.meta.env.VITE_API_URL ?? 'http://127.0.0.1:8000'

export type SourceId = 'modis' | 'noaa20' | 'noaa21'
export const SOURCE_IDS: SourceId[] = ['modis', 'noaa20', 'noaa21']

// Validated categorical palette (dark surface #101816, all-pairs CVD ΔE >= 9.4).
// Shape is a second encoding so identity never depends on color alone.
export const SOURCES: Record<SourceId, { label: string; short: string; color: string; shape: 'square' | 'circle' | 'diamond'; sensor: string }> = {
  modis: { label: 'MODIS (Terra + Aqua)', short: 'MODIS', color: '#d95926', shape: 'square', sensor: 'MODIS · 1 km' },
  noaa20: { label: 'VIIRS NOAA-20', short: 'NOAA-20', color: '#3987e5', shape: 'circle', sensor: 'VIIRS I-band · 375 m' },
  noaa21: { label: 'VIIRS NOAA-21', short: 'NOAA-21', color: '#199e70', shape: 'diamond', sensor: 'VIIRS I-band · 375 m' },
}

export type Filters = {
  sources: SourceId[]
  dateFrom: string
  dateTo: string
  minConfidence: '' | 'low' | 'nominal' | 'high'
  daynight: '' | 'D' | 'N'
}

export type Area =
  | { kind: 'bbox'; south: number; west: number; north: number; east: number }
  | { kind: 'polygon'; vertices: [number, number][] }

export type CrossCheck = {
  import_id: string
  retrieved_at: string
  api_rows: number
  rows_found_in_download: number
  api_rows_in_granules_present_in_download: number
  matched_within_shared_granules: number
  shared_granule_match_fraction: number | null
}

export type GlobalDataset = {
  id: SourceId
  label: string
  product: string
  firms_source: string
  classification: string
  original_filename: string
  acquisition: string
  sha256: string
  size_bytes: number
  row_count: number
  columns: string[]
  acquisition_dates: Record<string, number>
  date_range: { start: string; end: string }
  satellite_codes: Record<string, number>
  versions: Record<string, number>
  daynight: Record<string, number>
  duplicate_rows: number
  invalid_rows: number
  missing_values: Record<string, number>
  bounds: { south: number; north: number; west: number; east: number }
  api_cross_checks: CrossCheck[]
  notes: string[]
  nominal_pixel_km: number
}

export type FrpQuantiles = { n: number; min: number; p25: number; median: number; p75: number; p90: number; p99: number; max: number; mean: number; sum: number }

export type SourceStats = {
  label: string
  detections: number
  dataset_dates: string[]
  daily_detections: Record<string, number>
  daily_frp_sum_mw: Record<string, number>
  hourly_utc: number[]
  confidence: { low: number; nominal: number; high: number }
  daynight: { day: number; night: number }
  satellites: Record<string, number>
  frp: FrpQuantiles | null
  frp_histogram: { edges: number[]; counts: number[] }
  top_cells: { lat_center: number; lon_center: number; detections: number; frp_sum_mw: number }[]
  top_cell_degrees: number
}

export type StatsResponse = { dates: string[]; sources: Partial<Record<SourceId, SourceStats>>; note: string }

export type Side = {
  cell_overpasses: number
  co_detected: number
  other_viewed_area_no_detection: number
  other_coverage_unknown: number
  conditional_co_detection_rate: number | null
}

export type ErrorBlock = { bias_log10: number; median_abs_error_log10: number; rmse_log10: number; median_abs_error_factor: number }

export type CompareResponse = {
  source_a: SourceId
  source_b: SourceId
  labels: { a: string; b: string }
  satellite_filter: { a: string | null; b: string | null }
  cell_degrees: number
  window_minutes: number
  common_dates: string[]
  compared_dates: [string, string]
  detections: { a: number; b: number }
  a_side: Side
  b_side: Side
  timing: { pairs: number; median_abs_dt_min: number | null; dt_histogram: { edges: number[]; counts: number[] } }
  frp: {
    pairs: number
    pearson_r_log10?: number | null
    spearman_rho?: number | null
    median_ratio_b_over_a?: number
    median_ratio_95ci?: [number, number]
    median_pixels_per_pair?: { a: number; b: number }
    scatter_sample_log10?: [number, number][]
  }
  calibration: {
    status: 'fitted' | 'not_supported' | 'insufficient_data' | 'not_computed'
    reason?: string
    model?: string
    slope?: number | null
    intercept?: number | null
    slope_95ci?: [number, number]
    intercept_95ci?: [number, number]
    train_dates?: string[]
    validation_dates?: string[]
    train_pairs?: number
    validation_pairs?: number
    validation_fitted?: ErrorBlock
    validation_identity_baseline?: ErrorBlock
    validation_pearson_r_log10?: number | null
    improves_on_identity?: boolean
    caveats?: string[]
  }
  spatial_occupancy: { cells_a: number; cells_b: number; cells_both: number; jaccard: number | null; note: string }
}

export function filterParams(filters: Filters, area?: Area | null, includeSources = true): URLSearchParams {
  const params = new URLSearchParams()
  if (includeSources) filters.sources.forEach((source) => params.append('source', source))
  if (filters.dateFrom) params.set('date_from', filters.dateFrom)
  if (filters.dateTo) params.set('date_to', filters.dateTo)
  if (filters.minConfidence) params.set('min_confidence', filters.minConfidence)
  if (filters.daynight) params.set('daynight', filters.daynight)
  if (area?.kind === 'bbox') {
    params.set('south', area.south.toFixed(4))
    params.set('west', area.west.toFixed(4))
    params.set('north', area.north.toFixed(4))
    params.set('east', area.east.toFixed(4))
  } else if (area?.kind === 'polygon') {
    params.set('polygon', area.vertices.map(([lat, lon]) => `${lat.toFixed(4)},${lon.toFixed(4)}`).join(';'))
  }
  return params
}

export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API}${path}`, { signal })
  if (!response.ok) {
    let detail = `API returned ${response.status}`
    try {
      const body = await response.json()
      if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : body.detail.message ?? detail
    } catch { /* keep status text */ }
    throw new Error(detail)
  }
  return response.json() as Promise<T>
}

export function describeArea(area: Area | null): string {
  if (!area) return 'Global (all registered data)'
  if (area.kind === 'bbox') return `Box ${area.south.toFixed(2)}°…${area.north.toFixed(2)}° lat, ${area.west.toFixed(2)}°…${area.east.toFixed(2)}° lon`
  return `Polygon with ${area.vertices.length} vertices`
}

export const fmt = (value: number | null | undefined, digits = 0) =>
  value === null || value === undefined || Number.isNaN(value) ? '—' : value.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits })
