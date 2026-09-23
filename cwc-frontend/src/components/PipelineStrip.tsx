import type { DashboardStats } from '../api/client';

interface Props {
  stats: DashboardStats;
}

interface Segment {
  key: string;
  label: string;
  count: number;
  color: string;
}

/**
 * One bar in place of eight stat tiles.
 *
 * The tiles gave CLASSIFIED, IN REVIEW, OVERRIDE RATE and ACTION DUE the same
 * visual weight as the numbers that matter, and on a healthy run five of the
 * eight read 0 or 0%. A zero deserves to be legible, not prominent: segments
 * with nothing in them keep their label and lose their colour, so one document
 * landing in review is the only thing on the row that changes.
 */
export default function PipelineStrip({ stats }: Props) {
  const routed = stats.byStatus?.ROUTED ?? 0;
  const errored = stats.byStatus?.ERRORED ?? 0;
  const inReview = (stats.byBand?.LOW ?? 0) + (stats.byStatus?.CLASSIFIED ?? 0);

  const segments: Segment[] = [
    { key: 'filed',    label: 'Filed',     count: routed,   color: 'var(--ok)' },
    { key: 'review',   label: 'In review', count: inReview, color: 'var(--warn)' },
    { key: 'errored',  label: 'Errored',   count: errored,  color: 'var(--crit)' },
  ];

  const total = segments.reduce((sum, s) => sum + s.count, 0);

  return (
    <div className="strip">
      <div className="pipe">
        <div
          className="pipe-bar"
          role="img"
          aria-label={segments.map(s => `${s.label} ${s.count}`).join(', ')}
        >
          {total > 0 && segments.filter(s => s.count > 0).map(s => (
            <i key={s.key} style={{ width: `${(s.count / total) * 100}%`, background: s.color }} />
          ))}
        </div>
        <div className="pipe-key">
          {segments.map(s => (
            <span key={s.key} className={s.count === 0 ? 'is-zero' : undefined}>
              <i
                className="key-dot"
                style={{ background: s.count === 0 ? 'var(--line)' : s.color }}
              />
              {s.label} <b>{s.count}</b>
            </span>
          ))}
        </div>
      </div>

      <div className="stats">
        <div className="stat-box lift-sm">
          <div className="stat-k">Auto-routed</div>
          <div className="stat-v">{Math.round((stats.autoRouteRate ?? 0) * 100)}%</div>
        </div>
        <div className="stat-box lift-sm">
          <div className="stat-k">Avg time</div>
          <div className="stat-v">
            {stats.avgLatencyMs != null ? `${(stats.avgLatencyMs / 1000).toFixed(1)}s` : '—'}
          </div>
        </div>
        <div className="stat-box lift-sm">
          <div className="stat-k">Overridden</div>
          <div className="stat-v">{Math.round((stats.overrideRate ?? 0) * 100)}%</div>
        </div>
        {stats.actionDueSoon > 0 && (
          <div className="stat-box lift-sm">
            <div className="stat-k">Action due ≤7d</div>
            <div className="stat-v" style={{ color: 'var(--warn)' }}>{stats.actionDueSoon}</div>
          </div>
        )}
      </div>
    </div>
  );
}
