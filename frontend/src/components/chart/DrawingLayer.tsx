import type { KonvaEventObject } from 'konva/lib/Node'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Layer, Rect, Stage } from 'react-konva'
import { TOOL_BY_ID } from '../../lib/tools'
import type { Drawing, DrawingType, Point } from '../../lib/types'
import { useStore, type ToolId } from '../../store/useStore'
import { hitTest, layoutDrawing, type Transform } from './geometry'
import { ShapeNodes } from './Shapes'

interface Props {
  transform: Transform
  container: HTMLDivElement
  digits: number
}

interface Draft {
  type: DrawingType
  /** Anchors the trader has already committed with a click. */
  committed: Point[]
  /** The anchor currently under the pointer. */
  preview: Point
  /** Total anchors this tool needs. */
  needed: number
}

interface DragState {
  id: string
  mode: 'move' | 'anchor'
  anchorIndex: number
  origin: Point
  startPoints: Point[]
}

const ANCHOR_GRAB_RADIUS = 9

export default function DrawingLayer({ transform, container, digits }: Props) {
  const drawings = useStore((s) => s.drawings)
  const activeTool = useStore((s) => s.activeTool)
  const selectedId = useStore((s) => s.selectedDrawingId)
  const showDrawings = useStore((s) => s.showDrawings)
  const addDrawing = useStore((s) => s.addDrawing)
  const moveDrawing = useStore((s) => s.moveDrawing)
  const removeDrawing = useStore((s) => s.removeDrawing)
  const selectDrawing = useStore((s) => s.selectDrawing)
  const setActiveTool = useStore((s) => s.setActiveTool)

  const [draft, setDraft] = useState<Draft | null>(null)
  const [drag, setDrag] = useState<DragState | null>(null)
  const [hovered, setHovered] = useState<string | null>(null)
  const dragRef = useRef<DragState | null>(null)
  dragRef.current = drag

  const visible = useMemo(
    () => drawings.filter((d) => d.visible !== false),
    [drawings],
  )

  const layouts = useMemo(
    () => visible.map((drawing) => ({
      drawing,
      layout: layoutDrawing(drawing, transform, {
        selected: drawing.id === selectedId,
        digits,
      }),
    })),
    [visible, transform, selectedId, digits],
  )

  const toPoint = useCallback((x: number, y: number): Point => ({
    time: Math.round(transform.xToTime(x)),
    price: transform.yToPrice(y),
  }), [transform])

  // ── hover detection ───────────────────────────────────────
  // The Konva stage stays pass-through so the chart keeps its pan/zoom; it only
  // becomes interactive when the pointer is actually near a drawing.
  useEffect(() => {
    if (activeTool !== 'cursor' || !showDrawings) {
      setHovered(null)
      return
    }
    const onMove = (event: MouseEvent) => {
      const rect = container.getBoundingClientRect()
      const x = event.clientX - rect.left
      const y = event.clientY - rect.top
      if (x > transform.width || y > transform.height) {
        setHovered(null)
        return
      }
      const found = layouts.find(({ layout }) => hitTest(layout, x, y))
      setHovered(found ? found.drawing.id : null)
    }
    container.addEventListener('mousemove', onMove)
    return () => container.removeEventListener('mousemove', onMove)
  }, [container, layouts, activeTool, showDrawings, transform])

  // ── keyboard ──────────────────────────────────────────────
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return

      if (event.key === 'Escape') {
        setDraft(null)
        selectDrawing(null)
        setActiveTool('cursor')
        return
      }
      if ((event.key === 'Delete' || event.key === 'Backspace') && selectedId) {
        event.preventDefault()
        void removeDrawing(selectedId)
        return
      }
      const shortcut = [...TOOL_BY_ID.values()].find(
        (tool) => tool.shortcut && tool.shortcut.toLowerCase() === event.key.toLowerCase(),
      )
      if (shortcut && !event.metaKey && !event.ctrlKey) {
        setActiveTool(shortcut.id as ToolId)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selectedId, removeDrawing, selectDrawing, setActiveTool])

  // ── creation & editing ────────────────────────────────────
  const handleMouseDown = (event: KonvaEventObject<MouseEvent>) => {
    const stage = event.target.getStage()
    const pointer = stage?.getPointerPosition()
    if (!pointer) return
    const point = toPoint(pointer.x, pointer.y)

    if (activeTool === 'cursor') {
      // Prefer an anchor grab on the selected drawing, then a whole-shape move.
      const selected = layouts.find(({ drawing }) => drawing.id === selectedId)
      if (selected) {
        const anchor = selected.layout.anchors.find(
          (a) => Math.hypot(a.x - pointer.x, a.y - pointer.y) <= ANCHOR_GRAB_RADIUS,
        )
        if (anchor) {
          setDrag({
            id: selected.drawing.id, mode: 'anchor', anchorIndex: anchor.index,
            origin: point, startPoints: selected.drawing.points,
          })
          return
        }
      }

      const target = layouts.find(({ layout }) => hitTest(layout, pointer.x, pointer.y))
      if (!target) {
        selectDrawing(null)
        return
      }
      selectDrawing(target.drawing.id)
      if (!target.drawing.locked) {
        setDrag({
          id: target.drawing.id, mode: 'move', anchorIndex: -1,
          origin: point, startPoints: target.drawing.points,
        })
      }
      return
    }

    const spec = TOOL_BY_ID.get(activeTool)
    if (!spec) return

    // A follow-up click places the remaining anchors of a 3-point tool.
    if (draft) {
      const points = [...draft.committed, point]
      if (points.length >= draft.needed) {
        void addDrawing(draft.type, points)
        setDraft(null)
      } else {
        setDraft({ ...draft, committed: points, preview: point })
      }
      return
    }

    if (spec.anchors === 1) {
      if (activeTool === 'text') {
        const text = window.prompt('Note for the analyst')
        if (text) void addDrawing('text', [point], { text })
        setActiveTool('cursor')
      } else {
        void addDrawing(activeTool as DrawingType, [point])
      }
      return
    }

    setDraft({
      type: activeTool as DrawingType,
      committed: [point],
      preview: point,
      needed: spec.anchors === 'free' ? Number.POSITIVE_INFINITY : (spec.anchors as number),
    })
  }

  const handleMouseMove = (event: KonvaEventObject<MouseEvent>) => {
    const stage = event.target.getStage()
    const pointer = stage?.getPointerPosition()
    if (!pointer) return
    const point = toPoint(pointer.x, pointer.y)

    const current = dragRef.current
    if (current) {
      const dTime = point.time - current.origin.time
      const dPrice = point.price - current.origin.price
      const points = current.mode === 'move'
        ? current.startPoints.map((p) => ({ time: p.time + dTime, price: p.price + dPrice }))
        : current.startPoints.map((p, index) => (index === current.anchorIndex ? point : p))

      // Local echo keeps the drag at 60fps; the store write happens on release.
      useStore.setState((prev) => ({
        drawings: prev.drawings.map((d) => (d.id === current.id ? { ...d, points } : d)),
      }))
      return
    }

    if (!draft) return
    if (draft.type === 'brush') {
      setDraft({ ...draft, committed: [...draft.committed, point], preview: point })
    } else {
      setDraft({ ...draft, preview: point })
    }
  }

  const handleMouseUp = () => {
    const current = dragRef.current
    if (current) {
      const drawing = useStore.getState().drawings.find((d) => d.id === current.id)
      if (drawing) void moveDrawing(drawing.id, drawing.points)
      setDrag(null)
      return
    }

    if (!draft) return
    if (draft.type === 'brush') {
      if (draft.committed.length > 2) void addDrawing('brush', draft.committed)
      setDraft(null)
      return
    }

    const points = [...draft.committed, draft.preview]
    if (points.length >= draft.needed) {
      void addDrawing(draft.type, points)
      setDraft(null)
    } else {
      // A 3-anchor tool: bank this leg and wait for the next click.
      setDraft({ ...draft, committed: points })
    }
  }

  const draftPoints = draft
    ? (draft.type === 'brush' ? draft.committed : [...draft.committed, draft.preview])
    : []

  const draftDrawing: Drawing | null = draft && draftPoints.length > 0
    ? {
        id: '__draft__',
        symbol: '', timeframe: '',
        type: draft.type,
        points: draftPoints,
        style: {
          color: TOOL_BY_ID.get(draft.type)?.color ?? '#4C8DFF',
          width: 2, opacity: 1, extend_left: false, extend_right: true,
          show_labels: true, dash: [4, 4], fill: null,
        },
        locked: false, visible: true, meta: {},
        created_at: 0, updated_at: 0,
      }
    : null

  const interactive = activeTool !== 'cursor' || Boolean(draft) || Boolean(drag) ||
    Boolean(hovered && showDrawings)

  return (
    <div
      className="absolute left-0 top-0"
      style={{
        width: transform.width,
        height: transform.height,
        // lightweight-charts gives its own canvases z-index 1 and 2. Without an
        // explicit index above those, this layer renders through the chart's
        // transparent canvas but never receives a single pointer event.
        zIndex: 4,
        pointerEvents: interactive ? 'auto' : 'none',
        cursor: activeTool !== 'cursor' ? 'crosshair' : hovered ? 'pointer' : 'default',
      }}
    >
      <Stage
        width={transform.width}
        height={transform.height}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
      >
        <Layer>
          {/*
            Konva only dispatches pointer events when something is in the hit
            graph — with every node non-listening the stage stays silent. This
            invisible rect is that hit surface; the drawn shapes themselves
            stay non-listening so hit-testing remains ours (see geometry.ts).
          */}
          <Rect
            x={0}
            y={0}
            width={transform.width}
            height={transform.height}
            fill="transparent"
          />
          {showDrawings && layouts.map(({ drawing, layout }) => (
            <ShapeNodes key={drawing.id} keyPrefix={drawing.id} shapes={layout.shapes} />
          ))}
          {draftDrawing && (
            <ShapeNodes
              keyPrefix="draft"
              shapes={layoutDrawing(draftDrawing, transform, { digits }).shapes}
            />
          )}
        </Layer>
      </Stage>
    </div>
  )
}
