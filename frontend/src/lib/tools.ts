import type { DrawingType } from './types'

export interface ToolSpec {
  id: DrawingType | 'cursor'
  label: string
  hint: string
  /** Anchor points the tool needs. `brush` is free-form. */
  anchors: 1 | 2 | 3 | 'free'
  group: 'select' | 'lines' | 'zones' | 'measure' | 'annotate'
  color: string
  shortcut?: string
  icon: string
}

/** SVG path data drawn inside a 24×24 viewBox. */
export const TOOLS: ToolSpec[] = [
  {
    id: 'cursor', label: 'Select', hint: 'Select, move and edit drawings',
    anchors: 1, group: 'select', color: '#8B94A8', shortcut: 'V',
    icon: 'M5 3l14 8-6 1.5L10 19z',
  },
  {
    id: 'trendline', label: 'Trend Line', hint: 'Diagonal line across two swing points',
    anchors: 2, group: 'lines', color: '#4C8DFF', shortcut: 'T',
    icon: 'M4 19L20 5',
  },
  {
    id: 'horizontal', label: 'Horizontal', hint: 'Flat level at one price',
    anchors: 1, group: 'lines', color: '#8B94A8', shortcut: 'H',
    icon: 'M3 12h18',
  },
  {
    id: 'support', label: 'Support', hint: 'Level expected to hold beneath price',
    anchors: 2, group: 'lines', color: '#2FD3A0', shortcut: 'S',
    icon: 'M3 15h18M8 9l4 4 4-4',
  },
  {
    id: 'resistance', label: 'Resistance', hint: 'Level expected to cap price',
    anchors: 2, group: 'lines', color: '#FF7A59', shortcut: 'R',
    icon: 'M3 9h18M8 15l4-4 4 4',
  },
  {
    id: 'channel', label: 'Channel', hint: 'Two-point line with a parallel rail',
    anchors: 3, group: 'lines', color: '#9B8CFF', shortcut: 'C',
    icon: 'M3 16L15 4M8 21L20 9',
  },
  {
    id: 'parallel_channel', label: 'Parallel Channel', hint: 'Equidistant channel rails',
    anchors: 3, group: 'lines', color: '#9B8CFF',
    icon: 'M3 14h18M3 20h18M3 8h18',
  },
  {
    id: 'rectangle', label: 'Rectangle', hint: 'Highlight a price/time box',
    anchors: 2, group: 'zones', color: '#4C8DFF', shortcut: 'B',
    icon: 'M4 6h16v12H4z',
  },
  {
    id: 'supply_zone', label: 'Supply Zone', hint: 'Area where sellers took control',
    anchors: 2, group: 'zones', color: '#FF7A59',
    icon: 'M4 5h16v6H4zM7 16l5-3 5 3',
  },
  {
    id: 'demand_zone', label: 'Demand Zone', hint: 'Area where buyers took control',
    anchors: 2, group: 'zones', color: '#2FD3A0',
    icon: 'M4 13h16v6H4zM7 8l5 3 5-3',
  },
  {
    id: 'fib_retracement', label: 'Fib Retracement', hint: 'Pullback levels across a leg',
    anchors: 2, group: 'measure', color: '#F5C453', shortcut: 'F',
    icon: 'M3 5h18M3 10h18M3 15h13M3 20h18',
  },
  {
    id: 'fib_extension', label: 'Fib Extension', hint: 'Projection levels beyond a leg',
    anchors: 2, group: 'measure', color: '#F5C453',
    icon: 'M3 20h18M3 15h18M3 10h13M3 5h8',
  },
  {
    id: 'risk_reward', label: 'Risk / Reward', hint: 'Entry, stop and target box',
    anchors: 3, group: 'measure', color: '#2FD3A0',
    icon: 'M4 5h16v6H4zM4 13h16v6H4z',
  },
  {
    id: 'arrow', label: 'Arrow', hint: 'Point at something on the chart',
    anchors: 2, group: 'annotate', color: '#4C8DFF', shortcut: 'A',
    icon: 'M5 19L19 5M19 5h-7M19 5v7',
  },
  {
    id: 'text', label: 'Note', hint: 'Text the analyst will read',
    anchors: 1, group: 'annotate', color: '#E9EDF5', shortcut: 'N',
    icon: 'M5 5h14M12 5v14',
  },
  {
    id: 'brush', label: 'Brush', hint: 'Free-hand annotation',
    anchors: 'free', group: 'annotate', color: '#9B8CFF',
    icon: 'M4 18c4 0 3-8 7-8s3 6 7 6',
  },
]

export const TOOL_BY_ID = new Map(TOOLS.map((tool) => [tool.id, tool]))

export const FIB_RETRACEMENT_RATIOS = [0, 0.236, 0.382, 0.5, 0.618, 0.705, 0.786, 1]
export const FIB_EXTENSION_RATIOS = [0, 1, 1.272, 1.414, 1.618, 2, 2.618, 3.618]

/** Zones render as a filled band rather than a stroke. */
export const ZONE_TOOLS: DrawingType[] = ['rectangle', 'supply_zone', 'demand_zone']
