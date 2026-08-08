/**
 * Theme plumbing.
 *
 * Everything styled with Tailwind follows `data-theme` on <html> automatically,
 * because the palette is CSS variables. Canvas does not: lightweight-charts and
 * Konva both take literal colour strings, so they have to be read out of the
 * computed styles and reapplied whenever the theme changes.
 */
export type Theme = 'dark' | 'light'

const STORAGE_KEY = 'meridian.theme'

export function readStoredTheme(): Theme {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    if (stored === 'light' || stored === 'dark') return stored
  } catch {
    /* private browsing — fall through to the system preference */
  }
  return window.matchMedia?.('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
}

export function applyTheme(theme: Theme) {
  document.documentElement.setAttribute('data-theme', theme)
  try {
    window.localStorage.setItem(STORAGE_KEY, theme)
  } catch {
    /* not being able to remember the choice is not worth an error */
  }
}

/** Read a CSS custom property off <html>. */
export function cssVar(name: string, fallback = ''): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name)
  return value.trim() || fallback
}

/**
 * Read an `r g b` triplet variable as a css colour with alpha.
 *
 * Emits the legacy comma form on purpose: lightweight-charts parses colours
 * with its own regex and rejects the modern `rgb(r g b / a)` space syntax
 * outright ("Cannot parse color"), taking the whole chart down with it.
 */
export function rgbVar(name: string, alpha = 1): string {
  const [r, g, b] = cssVar(name, '255 255 255').split(/[\s,]+/)
  return alpha >= 1 ? `rgb(${r}, ${g}, ${b})` : `rgba(${r}, ${g}, ${b}, ${alpha})`
}

export interface ChartTheme {
  text: string
  grid: string
  border: string
  crosshair: string
  crosshairLabelBg: string
  bull: string
  bear: string
  wickUp: string
  wickDown: string
  priceLine: string
  volumeUp: string
  volumeDown: string
  labelBg: string
  ink: string
}

/** Snapshot the current theme's canvas colours. */
export function chartTheme(): ChartTheme {
  return {
    text: cssVar('--chart-text', '#8B94A8'),
    grid: cssVar('--chart-grid', 'rgba(255,255,255,0.035)'),
    border: cssVar('--chart-border', 'rgba(255,255,255,0.07)'),
    crosshair: cssVar('--chart-crosshair', 'rgba(233,237,245,0.28)'),
    crosshairLabelBg: rgbVar('--c-raised', 1),
    bull: rgbVar('--c-bull', 1),
    bear: rgbVar('--c-bear', 1),
    wickUp: rgbVar('--c-bull', 0.75),
    wickDown: rgbVar('--c-bear', 0.75),
    priceLine: rgbVar('--c-ink', 0.35),
    volumeUp: cssVar('--chart-volume-up', 'rgba(47,211,160,0.3)'),
    volumeDown: cssVar('--chart-volume-down', 'rgba(255,77,106,0.28)'),
    labelBg: cssVar('--overlay-label-bg', 'rgba(8,11,18,0.82)'),
    ink: rgbVar('--c-ink', 1),
  }
}
