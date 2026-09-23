/**
 * Shows confidence band AND forceManualReview as two separate, independent badges.
 *
 * Spec §5 Ruling 2: band derives solely from calibrated confidence and is never
 * overwritten by an override.  forceManualReview can be true at any band — e.g.
 * PRESCRIPTION is HIGH quality but still requires human sign-off.
 */

interface Props {
  band: string | null;
  forceManualReview: boolean | null;
  overrideReason: string | null;
}

const BAND_STYLE: Record<string, { label: string; bg: string; color: string }> = {
  HIGH:   { label: 'HIGH',   bg: 'var(--ok-soft)', color: 'var(--ok)' },
  MEDIUM: { label: 'MEDIUM', bg: 'var(--warn-soft)', color: 'var(--warn)' },
  LOW:    { label: 'LOW',    bg: 'var(--crit-soft)', color: 'var(--crit)' },
};

export default function ConfidenceBadge({ band, forceManualReview, overrideReason }: Props) {
  const bandCfg = band ? (BAND_STYLE[band] ?? { label: band, bg: 'var(--surface-3)', color: 'var(--ink-2)' }) : null;

  return (
    <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
      {/* Band badge — reflects calibrated confidence, independent of override */}
      {bandCfg && (
        <span style={{
          display: 'inline-block',
          padding: '2px 10px',
          borderRadius: 9999,
          fontSize: 12,
          fontWeight: 600,
          background: bandCfg.bg,
          color: bandCfg.color,
        }}>
          {bandCfg.label}
        </span>
      )}

      {/* Manual-review flag — independent of band; visible at every band level */}
      {forceManualReview && (
        <span
          title={overrideReason ?? 'Manual review required'}
          style={{
            display: 'inline-block',
            padding: '2px 8px',
            borderRadius: 9999,
            fontSize: 11,
            fontWeight: 600,
            background: 'var(--warn-soft)',
            color: 'var(--warn)',
            border: '1px solid color-mix(in srgb, var(--warn) 45%, transparent)',
            cursor: 'help',
          }}
        >
          REVIEW {overrideReason ? `(${overrideReason})` : ''}
        </span>
      )}
    </span>
  );
}
