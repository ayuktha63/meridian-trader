import { Circle, Label, Line, Rect, Tag, Text } from 'react-konva'
import { handleFill, labelPlate, type Shape } from './geometry'

/**
 * Renders the shared shape vocabulary produced by `geometry.ts`. Keeping this
 * in one place means user drawings and AI overlays are pixel-identical in
 * style, which is what makes them read as one coherent chart.
 */
export function ShapeNodes({ shapes, keyPrefix }: { shapes: Shape[]; keyPrefix: string }) {
  return (
    <>
      {shapes.map((shape, index) => {
        const key = `${keyPrefix}-${index}`

        switch (shape.kind) {
          case 'line':
            return (
              <Line
                key={key}
                points={[shape.x1, shape.y1, shape.x2, shape.y2]}
                stroke={shape.color}
                strokeWidth={shape.width}
                dash={shape.dash}
                opacity={shape.opacity ?? 1}
                lineCap="round"
                listening={false}
                perfectDrawEnabled={false}
              />
            )

          case 'rect':
            return (
              <Rect
                key={key}
                x={shape.x}
                y={shape.y}
                width={shape.w}
                height={shape.h}
                fill={shape.fill}
                opacity={shape.opacity ?? 1}
                stroke={shape.stroke}
                strokeWidth={shape.stroke && shape.stroke !== 'transparent' ? 1 : 0}
                dash={shape.dash}
                listening={false}
                perfectDrawEnabled={false}
              />
            )

          case 'poly':
            return (
              <Line
                key={key}
                points={shape.points}
                stroke={shape.width ? shape.color : undefined}
                strokeWidth={shape.width}
                fill={shape.fill}
                closed={shape.closed}
                dash={shape.dash}
                opacity={shape.opacity ?? 1}
                lineCap="round"
                lineJoin="round"
                tension={shape.closed ? 0 : 0.12}
                listening={false}
                perfectDrawEnabled={false}
              />
            )

          case 'label':
            return (
              <Label
                key={key}
                x={shape.x}
                y={shape.y}
                offsetX={shape.align === 'right' ? 0 : 0}
                listening={false}
              >
                <Tag
                  fill={shape.background ?? labelPlate()}
                  cornerRadius={3}
                  pointerDirection="none"
                />
                <Text
                  text={shape.text}
                  fontSize={10}
                  fontFamily="Inter, system-ui, sans-serif"
                  fill={shape.color}
                  padding={3}
                  align={shape.align ?? 'right'}
                  offsetX={shape.align === 'right' ? measure(shape.text) : 0}
                  offsetY={7}
                />
              </Label>
            )

          case 'handle':
            return (
              <Circle
                key={key}
                x={shape.x}
                y={shape.y}
                radius={4.5}
                fill={handleFill()}
                stroke={shape.color}
                strokeWidth={2}
                listening={false}
              />
            )

          default:
            return null
        }
      })}
    </>
  )
}

/** Cheap monospace-ish width estimate; exact metrics aren't worth a canvas measure here. */
function measure(text: string): number {
  return text.length * 5.6 + 6
}
