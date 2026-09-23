import { confidencePct } from '../api/client';

interface Props {
  /** 0..1 calibrated confidence. Null renders an em dash, never a full bar. */
  score: number | null;
  band: string | null;
  size?: 'sm' | 'lg';
}

/**
 * Confidence as a shape plus a number, not a word.
 *
 * Every row in a healthy run reads HIGH, at which point the badge has stopped
 * carrying information. A short bar makes 82% and 97% look different at a
 * glance — the difference that decides whether a person opens the document.
 *
 * The bar colour follows the BAND, not the raw percentage, so it can never
 * disagree with the gate: a document forced to manual review at 91% must not
 * be painted green just because the number is high.
 */
export default function ConfidenceMeter({ score, band, size = 'sm' }: Props) {
  if (score == null && !band) {
    return <span className="em-dash">—</span>;
  }

  const tone =
    band === 'LOW' ? ' is-low'
      : band === 'MEDIUM' ? ' is-med'
        : '';

  // No score but a band: show the band alone rather than inventing a width.
  const pct = score != null ? Math.max(0, Math.min(1, score)) : null;

  return (
    <span className={`meter${size === 'lg' ? ' meter-lg' : ''}`}>
      {pct != null && (
        <span className={`meter-track${tone}`}>
          <i style={{ width: `${pct * 100}%` }} />
        </span>
      )}
      <span className="meter-value">{confidencePct(score)}</span>
    </span>
  );
}
