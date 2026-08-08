import { cssVar, rgbVar } from '../../lib/theme'
import { FIB_EXTENSION_RATIOS, FIB_RETRACEMENT_RATIOS } from '../../lib/tools'
import type { Drawing, DrawingType, OverlayObject, Point } from '../../lib/types'

/** Backing plate behind canvas text — near-black on dark, near-white on light,
 *  so a caption stays legible whichever way the theme goes. */
export function labelPlate(): string {
  return cssVar('--overlay-label-bg', 'rgba(8,11,18,0.82)')
}

/** Fill inside a drag handle — the page background, so the ring reads as a ring. */
export function handleFill(): string {
  return rgbVar('--c-abyss', 1)
}

/**
 * Maps between chart space (time, price) and pane pixels.
 *
 * Both directions must extrapolate beyond the plotted data: AI overlays
 * project into the future, and a trendline dragged past the last candle still
 * needs a coordinate. lightweight-charts' own `timeToCoordinate` returns null
 * outside the data range, so the transform is derived from the visible logical
 * range instead — which is linear and always defined.
 */
export interface Transform {
  version: number
  width: number
  height: number
  timeToX(time: number): number
  xToTime(x: number): number
  priceToY(price: number): number
  yToPrice(y: number): number
}

export type Shape =
  | { kind: 'line'; x1: number; y1: number; x2: number; y2: number
      color: string; width: number; dash?: number[]; opacity?: number }
  | { kind: 'rect'; x: number; y: number; w: number; h: number
      fill?: string; stroke?: string; dash?: number[]; opacity?: number }
  | { kind: 'poly'; points: number[]; color: string; width: number
      closed?: boolean; fill?: string; opacity?: number; dash?: number[] }
  | { kind: 'label'; x: number; y: number; text: string; color: string
      align?: 'left' | 'right'; background?: string }
  | { kind: 'handle'; x: number; y: number; color: string; index: number }

export interface Layout {
  shapes: Shape[]
  /** Screen-space segments used for hit-testing. */
  hitSegments: [number, number, number, number][]
  hitRects: { x: number; y: number; w: number; h: number }[]
  anchors: { x: number; y: number; index: number }[]
}

const LABEL_INSET = 6

function sortPoints(points: Point[]): Point[] {
  return [...points].sort((a, b) => a.time - b.time)
}

function lineThrough(a: Point, b: Point, t: Transform, extendRight: boolean, width: number) {
  const x1 = t.timeToX(a.time)
  const y1 = t.priceToY(a.price)
  let x2 = t.timeToX(b.time)
  let y2 = t.priceToY(b.price)

  if (extendRight && x2 > x1) {
    // Project along the same gradient to the right edge of the pane.
    const slope = (y2 - y1) / (x2 - x1)
    y2 = y1 + slope * (width - x1)
    x2 = width
  }
  return { x1, y1, x2, y2 }
}

/**
 * Turns a drawing into renderable shapes plus hit-test geometry. Rendering and
 * hit-testing share this output so a drawing can never be visually somewhere
 * its click target is not.
 */
export function layoutDrawing(
  drawing: Drawing,
  transform: Transform,
  options: { selected?: boolean; digits?: number } = {},
): Layout {
  const { selected = false, digits = 5 } = options
  const shapes: Shape[] = []
  const hitSegments: [number, number, number, number][] = []
  const hitRects: { x: number; y: number; w: number; h: number }[] = []
  const anchors: { x: number; y: number; index: number }[] = []

  const points = drawing.points
  if (points.length === 0) return { shapes, hitSegments, hitRects, anchors }

  const color = drawing.style?.color ?? '#4C8DFF'
  const width = drawing.style?.width ?? 2
  const dash = drawing.style?.dash ?? undefined
  const type = drawing.type as DrawingType

  points.forEach((point, index) => {
    anchors.push({ x: transform.timeToX(point.time), y: transform.priceToY(point.price), index })
  })

  const pushLine = (x1: number, y1: number, x2: number, y2: number,
                    overrides: Partial<Extract<Shape, { kind: 'line' }>> = {}) => {
    shapes.push({ kind: 'line', x1, y1, x2, y2, color, width, dash, ...overrides })
    hitSegments.push([x1, y1, x2, y2])
  }

  const pushLabel = (x: number, y: number, text: string, labelColor = color,
                     align: 'left' | 'right' = 'right') => {
    if (drawing.style?.show_labels === false) return
    shapes.push({ kind: 'label', x, y, text, color: labelColor, align })
  }

  switch (type) {
    case 'horizontal':
    case 'support':
    case 'resistance': {
      const price = points[0].price
      const y = transform.priceToY(price)
      pushLine(0, y, transform.width, y)
      pushLabel(transform.width - LABEL_INSET, y,
        `${drawing.label ?? type} ${price.toFixed(digits)}`)
      break
    }

    case 'trendline': {
      if (points.length < 2) break
      const [a, b] = sortPoints(points)
      const { x1, y1, x2, y2 } = lineThrough(a, b, transform,
        drawing.style?.extend_right ?? true, transform.width)
      pushLine(x1, y1, x2, y2)
      if (drawing.label) pushLabel(x2 - LABEL_INSET, y2, drawing.label)
      break
    }

    case 'channel':
    case 'parallel_channel': {
      if (points.length < 2) break
      const sorted = sortPoints(points.slice(0, 2))
      const base = lineThrough(sorted[0], sorted[1], transform,
        drawing.style?.extend_right ?? true, transform.width)
      pushLine(base.x1, base.y1, base.x2, base.y2)

      if (points.length >= 3) {
        // The third anchor sets the rail offset, measured vertically.
        const railPoint = points[2]
        const baseSlope = (base.y2 - base.y1) / ((base.x2 - base.x1) || 1)
        const railX = transform.timeToX(railPoint.time)
        const baseYAtRail = base.y1 + baseSlope * (railX - base.x1)
        const offset = transform.priceToY(railPoint.price) - baseYAtRail
        pushLine(base.x1, base.y1 + offset, base.x2, base.y2 + offset)
        shapes.push({
          kind: 'poly',
          points: [base.x1, base.y1, base.x2, base.y2,
                   base.x2, base.y2 + offset, base.x1, base.y1 + offset],
          color: 'transparent', width: 0, closed: true, fill: color, opacity: 0.07,
        })
      }
      break
    }

    case 'rectangle':
    case 'supply_zone':
    case 'demand_zone': {
      if (points.length < 2) break
      const [a, b] = points
      const x1 = transform.timeToX(Math.min(a.time, b.time))
      const x2 = transform.timeToX(Math.max(a.time, b.time))
      const yTop = transform.priceToY(Math.max(a.price, b.price))
      const yBottom = transform.priceToY(Math.min(a.price, b.price))
      const rect = { x: x1, y: yTop, w: Math.max(2, x2 - x1), h: Math.max(2, yBottom - yTop) }
      shapes.push({ kind: 'rect', ...rect, fill: color, stroke: color, opacity: 0.16 })
      hitRects.push(rect)
      const title = drawing.label
        ?? (type === 'supply_zone' ? 'Supply' : type === 'demand_zone' ? 'Demand' : 'Zone')
      pushLabel(rect.x + rect.w - LABEL_INSET, rect.y + 9, title)
      break
    }

    case 'fib_retracement':
    case 'fib_extension': {
      if (points.length < 2) break
      const [a, b] = sortPoints(points)
      const ratios = type === 'fib_retracement' ? FIB_RETRACEMENT_RATIOS : FIB_EXTENSION_RATIOS
      const span = b.price - a.price
      const x1 = transform.timeToX(a.time)

      ratios.forEach((ratio) => {
        const price = type === 'fib_retracement'
          ? b.price - span * ratio
          : a.price + span * ratio
        const y = transform.priceToY(price)
        const golden = ratio === 0.618 || ratio === 1.618
        shapes.push({
          kind: 'line', x1, y1: y, x2: transform.width, y2: y,
          color, width: golden ? 1.6 : 1, dash: golden ? undefined : [3, 4],
          opacity: golden ? 0.95 : 0.55,
        })
        hitSegments.push([x1, y, transform.width, y])
        pushLabel(transform.width - LABEL_INSET, y,
          `${ratio.toFixed(3)}  ${price.toFixed(digits)}`)
      })
      // The measured leg itself.
      shapes.push({
        kind: 'line',
        x1, y1: transform.priceToY(a.price),
        x2: transform.timeToX(b.time), y2: transform.priceToY(b.price),
        color, width: 1, dash: [2, 3], opacity: 0.5,
      })
      break
    }

    case 'risk_reward': {
      if (points.length < 2) break
      const entry = points[0]
      const yEntry = transform.priceToY(entry.price)
      const x1 = transform.timeToX(entry.time)
      const others = points.slice(1)
      const prices = others.map((p) => p.price)
      const xEnd = Math.max(...points.map((p) => transform.timeToX(p.time)), x1 + 60)

      const above = Math.max(...prices, entry.price)
      const below = Math.min(...prices, entry.price)
      const long = (points[points.length - 1]?.price ?? entry.price) > entry.price
      const target = long ? above : below
      const stop = long ? below : above

      const rewardRect = {
        x: x1, y: transform.priceToY(Math.max(entry.price, target)),
        w: Math.max(2, xEnd - x1),
        h: Math.abs(transform.priceToY(target) - yEntry),
      }
      const riskRect = {
        x: x1, y: transform.priceToY(Math.max(entry.price, stop)),
        w: Math.max(2, xEnd - x1),
        h: Math.abs(transform.priceToY(stop) - yEntry),
      }
      shapes.push({ kind: 'rect', ...rewardRect, fill: '#2FD3A0', opacity: 0.14, stroke: '#2FD3A0' })
      shapes.push({ kind: 'rect', ...riskRect, fill: '#FF4D6A', opacity: 0.14, stroke: '#FF4D6A' })
      hitRects.push(rewardRect, riskRect)

      pushLine(x1, yEntry, xEnd, yEntry, { color: '#E9EDF5', width: 1.5, dash: [4, 3] })

      const risk = Math.abs(entry.price - stop)
      const reward = Math.abs(target - entry.price)
      pushLabel(xEnd - LABEL_INSET, yEntry,
        `R:R ${risk > 0 ? (reward / risk).toFixed(2) : '—'}`, '#E9EDF5')
      break
    }

    case 'arrow': {
      if (points.length < 2) break
      const [a, b] = points
      const x1 = transform.timeToX(a.time)
      const y1 = transform.priceToY(a.price)
      const x2 = transform.timeToX(b.time)
      const y2 = transform.priceToY(b.price)
      pushLine(x1, y1, x2, y2)

      const angle = Math.atan2(y2 - y1, x2 - x1)
      const head = 11
      shapes.push({
        kind: 'poly',
        points: [
          x2, y2,
          x2 - head * Math.cos(angle - Math.PI / 7), y2 - head * Math.sin(angle - Math.PI / 7),
          x2 - head * Math.cos(angle + Math.PI / 7), y2 - head * Math.sin(angle + Math.PI / 7),
        ],
        color, width: 0, closed: true, fill: color,
      })
      break
    }

    case 'text': {
      const x = transform.timeToX(points[0].time)
      const y = transform.priceToY(points[0].price)
      shapes.push({
        kind: 'label', x, y, align: 'left',
        text: drawing.text ?? drawing.label ?? 'Note',
        color: color === '#4C8DFF' ? '#E9EDF5' : color,
        background: labelPlate(),
      })
      hitRects.push({ x: x - 4, y: y - 10, w: 130, h: 20 })
      break
    }

    case 'brush': {
      if (points.length < 2) break
      const flat: number[] = []
      points.forEach((p) => {
        flat.push(transform.timeToX(p.time), transform.priceToY(p.price))
      })
      shapes.push({ kind: 'poly', points: flat, color, width })
      for (let i = 0; i + 3 < flat.length; i += 2) {
        hitSegments.push([flat[i], flat[i + 1], flat[i + 2], flat[i + 3]])
      }
      break
    }

    default:
      break
  }

  if (selected) {
    anchors.forEach((anchor) => {
      shapes.push({ kind: 'handle', x: anchor.x, y: anchor.y, color, index: anchor.index })
    })
  }

  return { shapes, hitSegments, hitRects, anchors }
}

/** Squared distance from a point to a segment — used for hover/hit testing. */
export function distanceToSegment(
  px: number, py: number, x1: number, y1: number, x2: number, y2: number,
): number {
  const dx = x2 - x1
  const dy = y2 - y1
  const lengthSq = dx * dx + dy * dy
  if (lengthSq === 0) return Math.hypot(px - x1, py - y1)
  let t = ((px - x1) * dx + (py - y1) * dy) / lengthSq
  t = Math.max(0, Math.min(1, t))
  return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))
}

export function hitTest(layout: Layout, x: number, y: number, tolerance = 7): boolean {
  for (const [x1, y1, x2, y2] of layout.hitSegments) {
    if (distanceToSegment(x, y, x1, y1, x2, y2) <= tolerance) return true
  }
  for (const rect of layout.hitRects) {
    if (x >= rect.x - tolerance && x <= rect.x + rect.w + tolerance &&
        y >= rect.y - tolerance && y <= rect.y + rect.h + tolerance) {
      // Only the border and interior of a zone count — big zones stay pannable
      // in their middle unless the pointer is genuinely inside.
      return true
    }
  }
  return false
}

/** Converts an AI overlay object into the same shape vocabulary. */
export function layoutOverlay(overlay: OverlayObject, transform: Transform,
                              digits: number): Shape[] {
  const shapes: Shape[] = []
  // A zone's colour may be a low-alpha band fill, which is unreadable as text.
  const color = overlay.color === 'transparent' ? '#C9D2E4' : overlay.color
  const dash = overlay.dash ?? undefined

  const startX = overlay.start_time !== null && overlay.start_time !== undefined
    ? transform.timeToX(overlay.start_time)
    : 0
  const endX = overlay.end_time !== null && overlay.end_time !== undefined
    ? transform.timeToX(overlay.end_time)
    : transform.width

  switch (overlay.kind) {
    case 'line':
    case 'ray': {
      if (overlay.price === null || overlay.price === undefined) break
      const y = transform.priceToY(overlay.price)
      shapes.push({
        kind: 'line', x1: Math.max(0, startX), y1: y, x2: endX, y2: y,
        color, width: overlay.width || 1, dash, opacity: overlay.opacity,
      })
      if (overlay.label) {
        shapes.push({
          kind: 'label', x: Math.min(endX, transform.width) - LABEL_INSET, y,
          text: overlay.label, color, align: 'right',
          background: labelPlate(),
        })
      }
      break
    }

    case 'zone':
    case 'box': {
      if (overlay.top === null || overlay.top === undefined ||
          overlay.bottom === null || overlay.bottom === undefined) break
      const yTop = transform.priceToY(overlay.top)
      const yBottom = transform.priceToY(overlay.bottom)
      shapes.push({
        kind: 'rect',
        x: Math.max(0, startX), y: yTop,
        w: Math.max(2, endX - Math.max(0, startX)),
        h: Math.max(2, yBottom - yTop),
        fill: overlay.fill ?? color,
        stroke: 'transparent',
        opacity: 1,
      })
      if (overlay.label) {
        shapes.push({
          kind: 'label', x: Math.max(0, startX) + 6, y: yTop + 9,
          text: overlay.label, color, align: 'left',
        })
      }
      break
    }

    case 'path': {
      if (overlay.points.length < 2) break
      const flat: number[] = []
      overlay.points.forEach((p) => {
        flat.push(transform.timeToX(p.time), transform.priceToY(p.price))
      })
      shapes.push({
        kind: 'poly', points: flat, color,
        width: overlay.width || 2, opacity: overlay.opacity, dash,
      })
      if (overlay.label) {
        // Anchored partway along rather than at the end: a projection runs to
        // the right edge of the pane, where a label placed on the last point
        // is clipped by the price scale.
        const anchor = Math.max(0, Math.floor(overlay.points.length * 0.6)) * 2
        shapes.push({
          kind: 'label', x: flat[anchor], y: flat[anchor + 1] - 9,
          text: overlay.label, color, align: 'left',
          background: labelPlate(),
        })
      }
      break
    }

    case 'arrow': {
      if (overlay.points.length < 2) break
      const [a, b] = overlay.points
      const x1 = transform.timeToX(a.time)
      const y1 = transform.priceToY(a.price)
      const x2 = transform.timeToX(b.time)
      const y2 = transform.priceToY(b.price)
      shapes.push({
        kind: 'line', x1, y1, x2, y2, color,
        width: overlay.width || 2, dash: [6, 4], opacity: 0.9,
      })
      const angle = Math.atan2(y2 - y1, x2 - x1)
      const head = 12
      shapes.push({
        kind: 'poly',
        points: [
          x2, y2,
          x2 - head * Math.cos(angle - Math.PI / 7), y2 - head * Math.sin(angle - Math.PI / 7),
          x2 - head * Math.cos(angle + Math.PI / 7), y2 - head * Math.sin(angle + Math.PI / 7),
        ],
        color, width: 0, closed: true, fill: color,
      })
      if (overlay.label) {
        shapes.push({
          kind: 'label', x: x2 - LABEL_INSET, y: y2 - 12,
          text: overlay.label, color, align: 'right',
        })
      }
      break
    }

    case 'marker': {
      if (overlay.price === null || overlay.price === undefined) break
      const y = transform.priceToY(overlay.price)
      const x = startX
      shapes.push({
        kind: 'label', x, y, align: 'left',
        text: overlay.label ?? overlay.price.toFixed(digits), color,
      })
      break
    }

    default:
      break
  }

  return shapes
}
