import { motion } from 'framer-motion'
import { useState } from 'react'
import { TOOLS } from '../../lib/tools'
import { useStore, type ToolId } from '../../store/useStore'

const GROUP_ORDER: { id: string; label: string }[] = [
  { id: 'select', label: 'Select' },
  { id: 'lines', label: 'Lines' },
  { id: 'zones', label: 'Zones' },
  { id: 'measure', label: 'Measure' },
  { id: 'annotate', label: 'Annotate' },
]

export default function Toolbar() {
  const activeTool = useStore((s) => s.activeTool)
  const setActiveTool = useStore((s) => s.setActiveTool)
  const drawings = useStore((s) => s.drawings)
  const clearDrawings = useStore((s) => s.clearDrawings)
  const showDrawings = useStore((s) => s.showDrawings)
  const toggleDrawingsVisible = useStore((s) => s.toggleDrawingsVisible)
  const [confirmClear, setConfirmClear] = useState(false)

  return (
    <div className="glass rounded-xl flex flex-col items-center gap-1 py-2 px-1.5 w-12 shrink-0">
      {GROUP_ORDER.map((group, groupIndex) => {
        const tools = TOOLS.filter((tool) => tool.group === group.id)
        if (tools.length === 0) return null
        return (
          <div key={group.id} className="flex flex-col items-center gap-1 w-full">
            {groupIndex > 0 && <div className="my-1 h-px w-6 bg-tint/[0.07]" />}
            {tools.map((tool) => {
              const active = activeTool === tool.id
              return (
                <button
                  key={tool.id}
                  type="button"
                  onClick={() => setActiveTool(tool.id as ToolId)}
                  title={`${tool.label}${tool.shortcut ? `  (${tool.shortcut})` : ''} — ${tool.hint}`}
                  aria-label={tool.label}
                  aria-pressed={active}
                  className={`relative h-8 w-8 rounded-lg grid place-items-center transition-all
                              duration-150 group
                              ${active
                                ? 'bg-accent/20 text-ink'
                                : 'text-faint hover:text-ink hover:bg-tint/[0.06]'}`}
                >
                  {active && (
                    <motion.span
                      layoutId="tool-active"
                      className="absolute inset-0 rounded-lg border border-accent/50"
                      transition={{ type: 'spring', stiffness: 380, damping: 32 }}
                    />
                  )}
                  <svg
                    viewBox="0 0 24 24" className="h-4 w-4 relative"
                    fill={tool.id === 'cursor' ? 'currentColor' : 'none'}
                    stroke={tool.id === 'cursor' ? 'none' : 'currentColor'}
                    strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round"
                  >
                    <path d={tool.icon} />
                  </svg>
                </button>
              )
            })}
          </div>
        )
      })}

      <div className="my-1 h-px w-6 bg-tint/[0.07]" />

      <button
        type="button"
        onClick={toggleDrawingsVisible}
        title={showDrawings ? 'Hide my drawings' : 'Show my drawings'}
        className={`h-8 w-8 rounded-lg grid place-items-center transition-colors
                    ${showDrawings ? 'text-muted hover:text-ink' : 'text-faint/50'}`}
      >
        <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor"
             strokeWidth={1.7} strokeLinecap="round">
          {showDrawings
            ? <><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7z" /><circle cx="12" cy="12" r="2.6" /></>
            : <><path d="M4 4l16 16" /><path d="M2 12s3.6-7 10-7c2 0 3.7.7 5.1 1.6M22 12s-3.6 7-10 7c-2 0-3.7-.7-5.1-1.6" /></>}
        </svg>
      </button>

      <button
        type="button"
        disabled={drawings.length === 0}
        onClick={() => {
          if (!confirmClear) {
            setConfirmClear(true)
            window.setTimeout(() => setConfirmClear(false), 2600)
            return
          }
          void clearDrawings()
          setConfirmClear(false)
        }}
        title={confirmClear
          ? 'Click again to delete every drawing on this chart'
          : `Clear ${drawings.length} drawing${drawings.length === 1 ? '' : 's'}`}
        className={`h-8 w-8 rounded-lg grid place-items-center transition-colors
                    disabled:opacity-25 disabled:pointer-events-none
                    ${confirmClear ? 'text-bear bg-bear/15' : 'text-faint hover:text-bear'}`}
      >
        <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor"
             strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round">
          <path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13" />
        </svg>
      </button>

      {drawings.length > 0 && (
        <span className="text-2xs tnum text-faint mt-0.5">{drawings.length}</span>
      )}
    </div>
  )
}
