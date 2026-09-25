import { useEffect, useState } from 'react'
import { feedbackApi, relativeTime, type Feedback, type Verdict } from '../api/client'

/**
 * Was this fax handled correctly?
 *
 * Thumbs up records immediately — one click, because the common case is "yes,
 * fine" and anything more is friction on a task done dozens of times a day.
 *
 * Thumbs down asks what went wrong before it saves. That summary is the whole
 * point: a count of failures tells you the system is wrong somewhere, while a
 * sentence telling you a cardiology consult went to Miscellaneous tells you
 * which routing rule to change.
 */
export default function FeedbackWidget(
  { trackingId, compact = false }: { trackingId: string; compact?: boolean }
) {
  const [current, setCurrent] = useState<Feedback | null>(null)
  const [loading, setLoading] = useState(true)
  const [pending, setPending] = useState<Verdict | null>(null)
  const [summary, setSummary] = useState('')
  const [saving, setSaving]   = useState(false)
  const [error, setError]     = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    feedbackApi.get(trackingId)
      .then(f => { if (!cancelled) { setCurrent(f); setSummary(f?.summary ?? '') } })
      .catch(() => { /* no verdict yet is the normal case */ })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [trackingId])

  async function save(verdict: Verdict, text?: string) {
    setSaving(true); setError(null)
    try {
      const saved = await feedbackApi.submit(trackingId, verdict, text)
      setCurrent(saved)
      setPending(null)
      setSummary(saved.summary ?? '')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save')
    } finally {
      setSaving(false)
    }
  }

  if (loading) return null

  // What the buttons show is the choice in play, not only the saved one:
  // opening the "something went wrong" form has to move the highlight off
  // Correct straight away, or the two look selected at once.
  const verdict = pending ?? current?.verdict ?? null

  return (
    <section className={compact ? 'feedback-inline' : 'card feedback-card'}>
      <header className={compact ? 'feedback-inline-head' : 'card-head'}>
        <h3>Was this handled correctly?</h3>
        {current && !compact && (
          <span className="muted small">
            recorded {relativeTime(current.updatedAt ?? current.createdAt)}
          </span>
        )}
      </header>

      <div className="feedback-actions">
        <button
          type="button"
          className={`feedback-btn${verdict === 'UP' ? ' is-selected up' : ''}`}
          disabled={saving}
          aria-pressed={verdict === 'UP'}
          onClick={() => { setPending(null); save('UP') }}
        >
          <span aria-hidden="true">👍</span> Correct
        </button>

        <button
          type="button"
          className={`feedback-btn${verdict === 'DOWN' ? ' is-selected down' : ''}`}
          disabled={saving}
          aria-pressed={verdict === 'DOWN'}
          onClick={() => { setPending('DOWN'); setSummary(current?.summary ?? '') }}
        >
          <span aria-hidden="true">👎</span> Something went wrong
        </button>
      </div>

      {/* Asking before saving, not after: a thumbs down with no explanation is
          the one case this feature exists to capture, so the text is required. */}
      {pending === 'DOWN' && (
        <div className="feedback-form">
          <label htmlFor="fb-summary">What went wrong?</label>
          <textarea
            id="fb-summary"
            rows={4}
            value={summary}
            disabled={saving}
            placeholder="e.g. This is a cardiology consult but it was filed under Miscellaneous. The sender is Chinatown Cardiology."
            onChange={e => setSummary(e.target.value)}
          />
          <p className="muted small">
            Saved with the file name, the name and folder the AI chose, and its
            confidence — so the routing rules can be corrected against it.
          </p>
          <div className="feedback-form-actions">
            <button
              type="button"
              className="btn primary"
              disabled={saving || summary.trim().length === 0}
              onClick={() => save('DOWN', summary.trim())}
            >
              {saving ? 'Saving…' : 'Save'}
            </button>
            <button
              type="button"
              className="btn"
              disabled={saving}
              onClick={() => { setPending(null); setSummary(current?.summary ?? '') }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {pending !== 'DOWN' && current?.verdict === 'DOWN' && current?.summary && (
        <blockquote className="feedback-summary">{current.summary}</blockquote>
      )}

      {error && <p className="error-text">{error}</p>}
    </section>
  )
}
