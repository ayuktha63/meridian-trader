/** @type {import('tailwindcss').Config} */

// Every colour resolves through a CSS variable so the whole palette swaps by
// setting `data-theme` on <html>. The `rgb(var(--x) / <alpha-value>)` form is
// what keeps Tailwind's opacity modifiers (`bg-panel/70`, `text-ink/85`) working
// against variables — a bare `var(--x)` silently breaks every one of them.
const v = (name) => `rgb(var(${name}) / <alpha-value>)`

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        void: v('--c-void'),
        abyss: v('--c-abyss'),
        panel: v('--c-panel'),
        raised: v('--c-raised'),
        hairline: 'rgb(var(--c-line) / var(--a-hairline))',
        edge: 'rgb(var(--c-line) / var(--a-edge))',
        ink: v('--c-ink'),
        muted: v('--c-muted'),
        faint: v('--c-faint'),
        bull: v('--c-bull'),
        bear: v('--c-bear'),
        accent: v('--c-accent'),
        warn: v('--c-warn'),
        violet: v('--c-violet'),

        // The tint behind every subtle wash and hover state. White on dark,
        // near-black on light — which is why the old `bg-white/[0.03]` had to
        // go: on a white background it is invisible.
        tint: v('--c-tint'),
      },
      fontFamily: {
        sans: ['Inter var', 'Inter', 'SF Pro Display', '-apple-system',
               'BlinkMacSystemFont', 'Segoe UI', 'system-ui', 'sans-serif'],
        mono: ['SF Mono', 'JetBrains Mono', 'Menlo', 'Consolas', 'monospace'],
      },
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem', letterSpacing: '0.02em' }],
      },
      boxShadow: {
        panel: 'var(--shadow-panel)',
        glow: '0 0 0 1px rgb(var(--c-accent) / 0.35), 0 8px 30px -10px rgb(var(--c-accent) / 0.45)',
      },
      backdropBlur: { xs: '2px' },
      keyframes: {
        pulseRing: {
          '0%': { opacity: '0.55', transform: 'scale(0.9)' },
          '70%': { opacity: '0', transform: 'scale(1.6)' },
          '100%': { opacity: '0' },
        },
        shimmer: {
          '0%': { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
      },
      animation: {
        'pulse-ring': 'pulseRing 2s cubic-bezier(0.4,0,0.6,1) infinite',
        shimmer: 'shimmer 2.4s linear infinite',
      },
    },
  },
  plugins: [],
}
