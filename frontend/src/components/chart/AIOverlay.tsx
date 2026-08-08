import { motion } from 'framer-motion'
import { useMemo } from 'react'
import { Layer, Stage } from 'react-konva'
import type { OverlayObject } from '../../lib/types'
import { layoutOverlay, type Shape, type Transform } from './geometry'
import { ShapeNodes } from './Shapes'

interface Props {
  overlays: OverlayObject[]
  transform: Transform
  digits: number
}

/** Zones paint under lines so strokes stay legible on top of fills. */
const PAINT_ORDER: Record<string, number> = {
  zone: 0, box: 0, path: 1, line: 2, ray: 2, arrow: 3, marker: 4, label: 4,
}

export default function AIOverlay({ overlays, transform, digits }: Props) {
  const shapes = useMemo<Shape[]>(() => {
    const ordered = [...overlays].sort(
      (a, b) => (PAINT_ORDER[a.kind] ?? 2) - (PAINT_ORDER[b.kind] ?? 2),
    )
    return ordered.flatMap((overlay) => layoutOverlay(overlay, transform, digits))
  }, [overlays, transform, digits])

  if (shapes.length === 0) return null

  return (
    <motion.div
      key={overlays.length}
      className="absolute left-0 top-0 pointer-events-none"
      // Above the chart's own canvases (z-index 1 and 2), below the drawing
      // layer so the trader's own work always sits on top of the AI's.
      style={{ width: transform.width, height: transform.height, zIndex: 3 }}
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.35, ease: 'easeOut' }}
    >
      <Stage width={transform.width} height={transform.height} listening={false}>
        <Layer listening={false}>
          <ShapeNodes keyPrefix="ai" shapes={shapes} />
        </Layer>
      </Stage>
    </motion.div>
  )
}
