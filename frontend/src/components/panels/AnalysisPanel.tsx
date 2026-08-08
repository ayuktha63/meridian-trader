import { useState } from 'react'
import { useStore } from '../../store/useStore'
import { Badge, EmptyState, SignedMeter, Spinner } from '../ui'

export default function AnalysisPanel() {
  const analysis = useStore((s) => s.analysis)
  const pending = useStore((s) => s.analysisPending)
  const askQuestion = useStore((s) => s.askQuestion)
  const aiEnabled = useStore((s) => s.aiEnabled)
  const [question, setQuestion] = useState('')
  const [showBrief, setShowBrief] = useState(false)

  if (!analysis) {
    return <EmptyState title="Waiting for the first analysis" hint="It runs automatically on every bar close." />
  }

  const { verdict, confidence } = analysis

  const submit = (event: React.FormEvent) => {
    event.preventDefault()
    if (!question.trim()) return
    void askQuestion(question.trim())
    setQuestion('')
  }

  return (
    <div className="h-full scroll-y px-3 py-3 space-y-4">
      {/* Confidence decomposition */}
      <section>
        <div className="flex items-center justify-between mb-2">
          <h3 className="panel-title">Confidence breakdown</h3>
          <span className="text-2xs tnum text-faint">
            net {confidence.directional_score >= 0 ? '+' : ''}
            {confidence.directional_score.toFixed(2)}
          </span>
        </div>
        <div className="space-y-2.5">
          {confidence.components.map((component) => (
            <SignedMeter
              key={component.name}
              label={component.name}
              value={component.raw_score}
              weight={component.weight}
              detail={component.detail}
            />
          ))}
        </div>
        <p className="mt-2.5 text-2xs text-faint leading-relaxed">{confidence.reason}</p>
      </section>

      <div className="divider" />

      {/* The two sides of the argument */}
      <section className="grid grid-cols-1 gap-3">
        <div>
          <h3 className="panel-title mb-1.5 text-bull/70">Bull case</h3>
          {verdict.bullish_points.length ? (
            <ul className="space-y-1">
              {verdict.bullish_points.map((point, index) => (
                <li key={index} className="flex gap-1.5 text-2xs text-ink/80 leading-relaxed">
                  <span className="text-bull mt-px shrink-0">▲</span>
                  <span>{point}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-2xs text-faint">Nothing supporting the long side.</p>
          )}
        </div>

        <div>
          <h3 className="panel-title mb-1.5 text-bear/70">Bear case</h3>
          {verdict.bearish_points.length ? (
            <ul className="space-y-1">
              {verdict.bearish_points.map((point, index) => (
                <li key={index} className="flex gap-1.5 text-2xs text-ink/80 leading-relaxed">
                  <span className="text-bear mt-px shrink-0">▼</span>
                  <span>{point}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-2xs text-faint">Nothing supporting the short side.</p>
          )}
        </div>
      </section>

      <div className="divider" />

      {/* Ask the analyst */}
      <section>
        <h3 className="panel-title mb-1.5">Ask the analyst</h3>
        <form onSubmit={submit} className="flex gap-1.5">
          <input
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder={aiEnabled
              ? 'e.g. is my resistance still valid?'
              : 'Set ANTHROPIC_API_KEY to enable'}
            disabled={!aiEnabled || pending}
            className="flex-1 min-w-0 rounded-lg bg-tint/[0.04] border border-hairline
                       px-2.5 py-1.5 text-2xs text-ink placeholder:text-faint/70
                       focus:border-accent/50 outline-none disabled:opacity-50"
          />
          <button type="submit" className="btn btn-primary shrink-0"
                  title="Ask Claude this question about the current chart"
                  disabled={!aiEnabled || pending || !question.trim()}>
            {pending ? <Spinner /> : 'Ask'}
          </button>
        </form>
        {!aiEnabled && (
          <p className="mt-1.5 text-2xs text-faint leading-relaxed">
            Running the deterministic confluence engine. Add an Anthropic API key to
            .env for Claude's reasoning.
          </p>
        )}
      </section>

      <div className="divider" />

      {/* Raw evidence brief — what the analyst actually received */}
      <section>
        <button
          type="button"
          onClick={() => setShowBrief((prev) => !prev)}
            title="Show the exact evidence brief that was sent to Claude"
          className="flex w-full items-center justify-between group"
        >
          <h3 className="panel-title group-hover:text-muted transition-colors">
            Evidence brief
          </h3>
          <Badge>{showBrief ? 'hide' : 'show'}</Badge>
        </button>
        {showBrief && (
          <pre className="mt-2 max-h-72 scroll-y rounded-lg bg-void/50 border border-hairline
                          p-2.5 text-[10px] leading-relaxed text-muted whitespace-pre-wrap
                          font-mono">
            {analysis.narrative}
          </pre>
        )}
      </section>
    </div>
  )
}
