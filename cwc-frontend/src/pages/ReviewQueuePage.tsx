import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { reviewApi, type FaxDetail, formatDateTime } from '../api/client';
import ConfidenceBadge from '../components/ConfidenceBadge';

export default function ReviewQueuePage() {
  const [items, setItems]   = useState<FaxDetail[]>([]);
  const [total, setTotal]   = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError]   = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const page = await reviewApi.queue();
      setItems(page.content);
      setTotal(page.totalElements);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load review queue');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Review Queue</h1>
        <p className="page-subtitle">
          {total > 0
            ? `${total} fax${total !== 1 ? 'es' : ''} awaiting review`
            : 'Faxes requiring human review before routing'}
        </p>
      </div>

      <div style={{ marginBottom: 12, display: 'flex', justifyContent: 'flex-end' }}>
        <button
          onClick={load}
          style={{
            background: 'var(--surface-3)', border: '1px solid var(--line)',
            borderRadius: 6, padding: '6px 14px', fontSize: 13,
            color: 'var(--ink-2)', cursor: 'pointer',
          }}
        >
          Refresh
        </button>
      </div>

      {error && (
        <div style={{
          padding: '12px 16px', borderRadius: 8, marginBottom: 16,
          background: 'var(--crit-soft)', border: '1px solid color-mix(in srgb, var(--crit) 45%, transparent)', color: 'var(--crit)', fontSize: 14,
        }}>
          {error}
        </div>
      )}

      {loading ? (
        <p style={{ color: 'var(--ink-3)' }}>Loading…</p>
      ) : items.length === 0 ? (
        <div className="placeholder-card">
          <h2>Queue is empty</h2>
          <p>No faxes are currently held for review. HIGH-confidence faxes with no overrides can be routed directly from the detail view.</p>
        </div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
            <thead>
              <tr style={{ background: 'var(--surface-3)' }}>
                {['File', 'Received', 'Category', 'Confidence', 'Hold Reason', 'Suggested Folder', ''].map(h => (
                  <th key={h} style={{
                    padding: '10px 14px', textAlign: 'left', fontWeight: 600,
                    color: 'var(--ink-2)', borderBottom: '2px solid var(--line)',
                  }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {items.map((fax, i) => (
                <tr
                  key={fax.trackingId}
                  style={{
                    background: i % 2 === 0 ? 'var(--surface)' : 'var(--surface-2)',
                    borderBottom: '1px solid var(--line)',
                  }}
                >
                  <td style={{ padding: '10px 14px', maxWidth: 220 }}>
                    <span title={fax.originalFileName} style={{
                      display: 'block', overflow: 'hidden',
                      textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                      fontFamily: 'monospace', fontSize: 12, color: 'var(--ink)',
                    }}>
                      {fax.originalFileName}
                    </span>
                  </td>
                  <td style={{ padding: '10px 14px', color: 'var(--ink-2)', whiteSpace: 'nowrap', fontSize: 12 }}>
                    {formatDateTime(fax.receivedAt)}
                  </td>
                  <td style={{ padding: '10px 14px', color: 'var(--ink-2)' }}>
                    <div style={{ fontWeight: 600, fontSize: 13 }}>{fax.category ?? '—'}</div>
                    {fax.subtype && (
                      <div style={{ fontSize: 11, color: 'var(--ink-3)' }}>{fax.subtype}</div>
                    )}
                  </td>
                  <td style={{ padding: '10px 14px' }}>
                    <ConfidenceBadge
                      band={fax.confidenceBand}
                      forceManualReview={fax.forceManualReview}
                      overrideReason={fax.overrideReason}
                    />
                  </td>
                  <td style={{ padding: '10px 14px', fontSize: 12 }}>
                    {fax.overrideReason
                      ? <span style={{
                          background: 'var(--warn-soft)', border: '1px solid color-mix(in srgb, var(--warn) 45%, transparent)',
                          color: 'var(--warn)', borderRadius: 4, padding: '2px 8px',
                        }}>
                          {fax.overrideReason}
                        </span>
                      : fax.confidenceBand === 'LOW'
                        ? <span style={{
                            background: 'var(--crit-soft)', border: '1px solid color-mix(in srgb, var(--crit) 45%, transparent)',
                            color: 'var(--crit)', borderRadius: 4, padding: '2px 8px',
                          }}>
                            LOW_CONFIDENCE
                          </span>
                        : <span style={{ color: 'var(--ink-3)' }}>—</span>
                    }
                  </td>
                  <td style={{ padding: '10px 14px', fontFamily: 'monospace', fontSize: 12, color: 'var(--ink-2)' }}>
                    {fax.suggestedFolder ?? '—'}
                  </td>
                  <td style={{ padding: '10px 14px' }}>
                    <Link
                      to={`/faxes/${fax.trackingId}`}
                      style={{ color: 'var(--accent)', textDecoration: 'none', fontWeight: 500, fontSize: 13 }}
                    >
                      Review
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
