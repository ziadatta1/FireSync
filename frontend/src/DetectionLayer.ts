import L from 'leaflet'
import { SOURCES, type SourceId } from './api.ts'

export type DrawItem = {
  source: SourceId
  lat: number
  lon: number
  count: number // detections in cell (1 for an individual detection)
  frp: number // frp sum for cells, frp for points
  frpMax: number
  sourceRow?: number // only for individual detections
  extra?: (string | number | null)[]
}

// Sources sit at fixed sub-positions inside a cell so co-located layers stay visible.
const OFFSETS: Record<SourceId, [number, number]> = { modis: [0, -0.28], noaa20: [-0.26, 0.2], noaa21: [0.26, 0.2] }
const SURFACE = '#0b1210'

function drawShape(ctx: CanvasRenderingContext2D, shape: string, x: number, y: number, r: number) {
  ctx.beginPath()
  if (shape === 'square') ctx.rect(x - r, y - r, r * 2, r * 2)
  else if (shape === 'diamond') {
    const d = r * 1.3
    ctx.moveTo(x, y - d); ctx.lineTo(x + d, y); ctx.lineTo(x, y + d); ctx.lineTo(x - d, y); ctx.closePath()
  } else ctx.arc(x, y, r, 0, Math.PI * 2)
}

/** Canvas overlay that draws tens of thousands of cells/detections without DOM markers. */
export class DetectionLayer extends L.Layer {
  private canvas?: HTMLCanvasElement
  private items: DrawItem[] = []
  private mode: 'cells' | 'points' = 'cells'
  private cellDeg = 1
  private screen: { x: number; y: number; r: number; item: DrawItem }[] = []
  private highlight: DrawItem | null = null

  setData(items: DrawItem[], mode: 'cells' | 'points', cellDeg = 1) {
    this.items = items
    this.mode = mode
    this.cellDeg = cellDeg
    this.redraw()
  }

  setHighlight(item: DrawItem | null) {
    this.highlight = item
    this.redraw()
  }

  onAdd(map: L.Map) {
    this.canvas = L.DomUtil.create('canvas', 'detection-canvas leaflet-zoom-hide') as HTMLCanvasElement
    map.getPanes().overlayPane.appendChild(this.canvas)
    map.on('moveend zoomend resize viewreset', this.redraw, this)
    this.redraw()
    return this
  }

  onRemove(map: L.Map) {
    map.off('moveend zoomend resize viewreset', this.redraw, this)
    this.canvas?.remove()
    this.canvas = undefined
    return this
  }

  hitTest(point: L.Point, tolerance = 6): DrawItem | null {
    let best: DrawItem | null = null
    let bestDistance = Infinity
    for (const entry of this.screen) {
      const distance = Math.hypot(entry.x - point.x, entry.y - point.y)
      if (distance <= entry.r + tolerance && distance < bestDistance) {
        best = entry.item
        bestDistance = distance
      }
    }
    return best
  }

  itemsNear(item: DrawItem): DrawItem[] {
    return this.items.filter((other) => other.lat === item.lat && other.lon === item.lon)
  }

  redraw() {
    const map = this._map as L.Map | undefined
    const canvas = this.canvas
    if (!map || !canvas) return
    const size = map.getSize()
    const ratio = window.devicePixelRatio || 1
    L.DomUtil.setPosition(canvas, map.containerPointToLayerPoint([0, 0]))
    canvas.width = size.x * ratio
    canvas.height = size.y * ratio
    canvas.style.width = `${size.x}px`
    canvas.style.height = `${size.y}px`
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0)
    ctx.clearRect(0, 0, size.x, size.y)
    this.screen = []
    const zoom = map.getZoom()
    const bounds = map.getBounds().pad(0.05)
    // Larger marks first so small ones stay visible on top.
    const ordered = [...this.items].sort((a, b) => b.count - a.count)
    const cellPx = (this.cellDeg * 256 * Math.pow(2, zoom)) / 360
    for (const item of ordered) {
      if (!bounds.contains([item.lat, item.lon])) continue
      const p = map.latLngToContainerPoint([item.lat, item.lon])
      const [ox, oy] = this.mode === 'cells' ? OFFSETS[item.source] : [0, 0]
      const x = p.x + ox * cellPx
      const y = p.y + oy * cellPx
      // Cells: size grows with log10(count) up to ~0.4 of the cell width (min 1.4 px, max 11 px).
      const r = this.mode === 'cells'
        ? Math.max(1.4, Math.min(11, cellPx * 0.4 * (0.2 + 0.8 * Math.min(1, Math.log10(item.count + 1) / 3))))
        : Math.min(8, 3 + Math.log10(Math.max(item.frp, 1)) * 1.6)
      const spec = SOURCES[item.source]
      ctx.globalAlpha = this.mode === 'cells' ? 0.78 : 0.9
      drawShape(ctx, spec.shape, x, y, r)
      ctx.fillStyle = spec.color
      ctx.fill()
      ctx.globalAlpha = 1
      if (r >= 2.5) {
        ctx.lineWidth = 1
        ctx.strokeStyle = SURFACE
        ctx.stroke()
      }
      this.screen.push({ x, y, r, item })
    }
    if (this.highlight) {
      const p = map.latLngToContainerPoint([this.highlight.lat, this.highlight.lon])
      ctx.beginPath()
      ctx.arc(p.x, p.y, 13, 0, Math.PI * 2)
      ctx.lineWidth = 2
      ctx.strokeStyle = '#f2f5ef'
      ctx.stroke()
    }
  }
}
