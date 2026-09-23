import { useCallback, useEffect, useState } from 'react';

// ── Types matching run_eval.py JSON output ────────────────────────────────────
interface PerFileResult {
  file: string;
  error?: string;
  expected_category: string;
  predicted_category: string;
  expected_subtype: string | null;
  predicted_subtype: string | null;
  expected_folder: string;
  predicted_folder: string;
  rule_matched: string;
  calibrated_confidence: number | null;
  confidence_band: string | null;
  force_manual_review: boolean;
  auto_routable: boolean;
  category_pass: boolean;
  subtype_pass: boolean;
  folder_pass: boolean;
  latency_ms: number;
  evidence: string[];
  reason: string | null;
  runner_up_category: string | null;
  runner_up_confidence: number | null;
  // multi-repeat fields
  deterministic?: boolean;
  repeat_categories?: string[];
  mean_confidence?: number;
  confidence_std?: number;
  mean_latency_ms?: number;
  n_repeat?: number;
}

interface GateQuality {
  auto_correct: number;
  auto_wrong: number;
  review_correct: number;
  review_wrong: number;
}

interface ConfusionMatrix {
  labels: string[];
  matrix: number[][];
}

interface EvalReport {
  timestamp: string;
  caveat: string;
  n_samples: number;
  n_repeat: number;
  routing_mode: string;
  summary: {
    category_accuracy: number;
    subtype_accuracy: number;
    folder_accuracy: number;
    category_pass: number;
    subtype_pass: number;
    folder_pass: number;
    n_samples: number;
  };
  gate_quality: GateQuality;
  confusion_matrix: ConfusionMatrix;
  per_file: PerFileResult[];
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function pct(v: number) { return `${Math.round(v * 100)}%`; }

function PassBadge({ pass }: { pass: boolean }) {
  return (
    <span style={{
      background: pass ? 'var(--ok-soft)' : 'var(--crit-soft)',
      color: pass ? 'var(--ok)' : 'var(--crit)',
      border: `1px solid ${pass ? 'color-mix(in srgb, var(--ok) 45%, transparent)' : 'color-mix(in srgb, var(--crit) 45%, transparent)'}`,
      borderRadius: 4, padding: '2px 8px', fontSize: 12, fontWeight: 700,
    }}>
      {pass ? '✓' : '✗'}
    </span>
  );
}

function BandPill({ band }: { band: string | null }) {
  if (!band) return <span style={{ color: 'var(--ink-3)' }}>—</span>;
  const colors: Record<string, [string, string]> = {
    HIGH:   ['var(--ok-soft)', 'var(--ok)'],
    MEDIUM: ['var(--warn-soft)', 'var(--warn)'],
    LOW:    ['var(--crit-soft)', 'var(--crit)'],
  };
  const [bg, color] = colors[band] ?? ['var(--surface-3)', 'var(--ink-2)'];
  return (
    <span style={{
      background: bg, color, border: '1px solid currentColor',
      borderRadius: 12, padding: '2px 10px', fontSize: 12, fontWeight: 600,
    }}>
      {band}
    </span>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────

function CaveatBox({ text }: { text: string }) {
  return (
    <div style={{
      background: 'var(--warn-soft)',
      border: '2px solid var(--warn)',
      borderRadius: 10,
      padding: '20px 24px',
      marginBottom: 24,
    }}>
      <div style={{
        fontWeight: 800, fontSize: 16, color: 'var(--warn)', marginBottom: 10,
        display: 'flex', alignItems: 'center', gap: 8,
      }}>
        ⚠ Read Before Citing These Numbers
      </div>
      <p style={{ margin: 0, fontSize: 14, color: 'var(--warn)', lineHeight: 1.7 }}>
        {text}
      </p>
    </div>
  );
}

function SummaryRow({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div style={{
      background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 8,
      padding: '16px 20px', flex: '1 1 140px',
    }}>
      <div style={{ fontSize: 12, color: 'var(--ink-3)', fontWeight: 600, marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 28, fontWeight: 800, color: 'var(--ink)' }}>{value}</div>
      {sub && <div style={{ fontSize: 12, color: 'var(--ink-3)', marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

function GateQualityTable({ gq }: { gq: GateQuality }) {
  const rows: Array<{ label: string; count: number; highlight?: string; note?: string }> = [
    { label: 'Auto-routable AND correct', count: gq.auto_correct, highlight: 'var(--ok-soft)', note: '(the win)' },
    { label: 'Auto-routable AND WRONG', count: gq.auto_wrong,
      highlight: gq.auto_wrong > 0 ? 'var(--crit-soft)' : 'var(--ok-soft)',
      note: '→ DRIVE TO ZERO' },
    { label: 'Held for review — would have been correct', count: gq.review_correct,
      highlight: 'var(--warn-soft)', note: '(cost of caution)' },
    { label: 'Held for review — correctly held', count: gq.review_wrong,
      highlight: 'var(--ok-soft)', note: '(correctly cautious)' },
  ];
  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
        <thead>
          <tr style={{ background: 'var(--surface-2)' }}>
            <th style={{ padding: '10px 14px', textAlign: 'left', color: 'var(--ink-2)',
              borderBottom: '2px solid var(--line)', fontWeight: 700 }}>Outcome</th>
            <th style={{ padding: '10px 14px', textAlign: 'center', color: 'var(--ink-2)',
              borderBottom: '2px solid var(--line)', fontWeight: 700 }}>Count</th>
            <th style={{ padding: '10px 14px', textAlign: 'left', color: 'var(--ink-2)',
              borderBottom: '2px solid var(--line)', fontWeight: 700 }}>Note</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} style={{ background: row.highlight, borderBottom: '1px solid var(--line)' }}>
              <td style={{ padding: '10px 14px', color: 'var(--ink)', fontWeight: row.count > 0 && i === 1 ? 700 : 400 }}>
                {row.label}
              </td>
              <td style={{
                padding: '10px 14px', textAlign: 'center', fontSize: 20, fontWeight: 800,
                color: i === 1 ? (row.count > 0 ? 'var(--crit)' : 'var(--ok)') : 'var(--ink)',
              }}>
                {row.count}
              </td>
              <td style={{ padding: '10px 14px', color: 'var(--ink-3)', fontSize: 12 }}>
                {row.note}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ConfusionMatrixTable({ cm }: { cm: ConfusionMatrix }) {
  if (!cm.labels.length) return <p style={{ color: 'var(--ink-3)' }}>No data</p>;
  const short = (s: string) => s.replace(/_/g, ' ');
  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ borderCollapse: 'collapse', fontSize: 12 }}>
        <thead>
          <tr>
            <th style={{ padding: '6px 10px', color: 'var(--ink-2)', textAlign: 'left' }}>
              Exp ↓ / Pred →
            </th>
            {cm.labels.map(l => (
              <th key={l} style={{
                padding: '6px 10px', color: 'var(--ink-2)', fontWeight: 600,
                maxWidth: 90, wordBreak: 'break-word', textAlign: 'center',
                fontSize: 11,
              }}>
                {short(l)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {cm.matrix.map((row, i) => (
            <tr key={i}>
              <td style={{ padding: '6px 10px', fontWeight: 700, color: 'var(--ink-2)',
                whiteSpace: 'nowrap', fontSize: 11 }}>
                {short(cm.labels[i])}
              </td>
              {row.map((cell, j) => (
                <td key={j} style={{
                  padding: '6px 10px', textAlign: 'center', fontWeight: cell > 0 ? 700 : 400,
                  background: i === j && cell > 0 ? 'var(--ok-soft)'
                    : (i !== j && cell > 0) ? 'var(--crit-soft)' : 'var(--surface)',
                  border: '1px solid var(--line)',
                  color: i === j ? 'var(--ok)' : (cell > 0 ? 'var(--crit)' : 'var(--ink-3)'),
                }}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <p style={{ fontSize: 11, color: 'var(--ink-3)', marginTop: 8 }}>
        Diagonal = correct. Off-diagonal = misclassified.
      </p>
    </div>
  );
}

function PerFileRow({ r }: { r: PerFileResult }) {
  const conf = r.calibrated_confidence != null
    ? `${Math.round(r.calibrated_confidence * 100)}%` : '—';
  const evidence = Array.isArray(r.evidence) ? r.evidence : [];

  return (
    <div style={{
      background: r.category_pass ? 'var(--surface)' : 'var(--crit-soft)',
      border: `1px solid ${r.category_pass ? 'var(--line)' : 'color-mix(in srgb, var(--crit) 45%, transparent)'}`,
      borderRadius: 8, padding: '16px 20px', marginBottom: 12,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
        <span style={{ fontFamily: 'monospace', fontSize: 13, fontWeight: 700, color: 'var(--ink)' }}>
          {r.file}
        </span>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span style={{ fontSize: 12, color: 'var(--ink-3)' }}>{conf}</span>
          <BandPill band={r.confidence_band} />
          {r.force_manual_review && (
            <span style={{
              background: 'var(--warn-soft)', color: 'var(--warn)', border: '1px solid color-mix(in srgb, var(--warn) 45%, transparent)',
              borderRadius: 4, padding: '2px 8px', fontSize: 11, fontWeight: 600,
            }}>REVIEW</span>
          )}
          <span style={{ fontSize: 12, color: 'var(--ink-3)' }}>{r.latency_ms}ms</span>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10, marginTop: 12 }}>
        {([
          ['Category', r.expected_category, r.predicted_category, r.category_pass],
          ['Subtype',  r.expected_subtype,  r.predicted_subtype,  r.subtype_pass],
          ['Folder',   r.expected_folder,   r.predicted_folder,   r.folder_pass],
        ] as const).map(([label, exp, pred, pass]) => (
          <div key={label} style={{
            background: 'var(--surface-2)', borderRadius: 6, padding: '10px 12px',
          }}>
            <div style={{ fontSize: 11, color: 'var(--ink-3)', fontWeight: 600, marginBottom: 6 }}>
              {label} <PassBadge pass={!!pass} />
            </div>
            <div style={{ fontSize: 12, color: 'var(--ink-3)' }}>Expected: {exp ?? '—'}</div>
            <div style={{
              fontSize: 13, fontWeight: 700,
              color: pass ? 'var(--ok)' : 'var(--crit)',
            }}>
              Predicted: {pred ?? '—'}
            </div>
          </div>
        ))}
      </div>

      {evidence.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--ink-2)', marginBottom: 4 }}>Evidence</div>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {evidence.slice(0, 3).map((e, i) => (
              <li key={i} style={{ fontSize: 12, color: 'var(--ink-2)', marginBottom: 2 }}>
                <q style={{ fontStyle: 'italic' }}>{e}</q>
              </li>
            ))}
          </ul>
        </div>
      )}

      {!r.category_pass && r.reason && (
        <div style={{
          marginTop: 10, background: 'var(--warn-soft)', borderRadius: 6,
          padding: '8px 12px', fontSize: 12, color: 'var(--warn)',
        }}>
          <strong>Model reason:</strong> {r.reason}
        </div>
      )}

      {r.runner_up_category && (
        <div style={{ marginTop: 6, fontSize: 11, color: 'var(--ink-3)' }}>
          Runner-up: {r.runner_up_category}
          {r.runner_up_confidence != null && ` (${Math.round(r.runner_up_confidence * 100)}%)`}
        </div>
      )}

      {r.deterministic === false && (
        <div style={{
          marginTop: 8, background: 'var(--crit-soft)', borderRadius: 6, padding: '6px 10px',
          fontSize: 12, color: 'var(--crit)', fontWeight: 600,
        }}>
          Non-deterministic across {r.n_repeat} runs: {r.repeat_categories?.join(', ')}
        </div>
      )}
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function AccuracyPage() {
  const [report, setReport]   = useState<EvalReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch('/api/eval/report');
      if (res.status === 404) {
        setReport(null);
        setError(null);
      } else if (!res.ok) {
        throw new Error(`${res.status} ${res.statusText}`);
      } else {
        setReport(await res.json());
        setError(null);
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load report');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  return (
    <div>
      <div className="page-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end' }}>
        <div>
          <h1 className="page-title">Accuracy</h1>
          <p className="page-subtitle">
            Golden-set evaluation — 5 samples, internal consistency only
          </p>
        </div>
        <button
          onClick={load}
          style={{
            background: 'var(--surface-3)', border: '1px solid var(--line)',
            borderRadius: 6, padding: '7px 16px', fontSize: 13, color: 'var(--ink-2)',
            cursor: 'pointer',
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
      ) : !report ? (
        <div className="placeholder-card">
          <h2>No evaluation report yet</h2>
          <p>
            Run <code>python evaluation/run_eval.py</code> from the repo root,
            then click <strong>Refresh</strong> above.
          </p>
          <p style={{ fontSize: 13, color: 'var(--ink-3)', marginTop: 8 }}>
            The report is saved to <code>evaluation/reports/</code> and served here automatically.
          </p>
        </div>
      ) : (
        <>
          {/* Caveat — prominent, not small print */}
          <CaveatBox text={report.caveat} />

          {/* Report metadata */}
          <div style={{ fontSize: 12, color: 'var(--ink-3)', marginBottom: 20 }}>
            Generated: {new Date(report.timestamp).toLocaleString()} &nbsp;·&nbsp;
            Samples: {report.n_samples} &nbsp;·&nbsp;
            Repeats: {report.n_repeat} &nbsp;·&nbsp;
            Routing mode: {report.routing_mode}
          </div>

          {/* Summary pills */}
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 28 }}>
            <SummaryRow
              label="Category Accuracy"
              value={pct(report.summary.category_accuracy)}
              sub={`${report.summary.category_pass}/${report.summary.n_samples} correct`}
            />
            <SummaryRow
              label="Subtype Accuracy"
              value={pct(report.summary.subtype_accuracy)}
              sub={`${report.summary.subtype_pass}/${report.summary.n_samples} correct`}
            />
            <SummaryRow
              label="Folder Accuracy"
              value={pct(report.summary.folder_accuracy)}
              sub={`${report.summary.folder_pass}/${report.summary.n_samples} correct`}
            />
          </div>

          {/* Gate Quality */}
          <section style={{ marginBottom: 28 }}>
            <h2 style={{ fontSize: 16, fontWeight: 700, color: 'var(--ink)', marginBottom: 6 }}>
              Gate Quality
            </h2>
            <p style={{ fontSize: 13, color: 'var(--ink-3)', marginBottom: 12 }}>
              Gate quality matters more than raw accuracy: a wrong auto-route creates real work.
              The number of auto-routable-and-wrong documents must be driven to zero.
            </p>
            <GateQualityTable gq={report.gate_quality} />
          </section>

          {/* Confusion Matrix */}
          <section style={{ marginBottom: 28 }}>
            <h2 style={{ fontSize: 16, fontWeight: 700, color: 'var(--ink)', marginBottom: 12 }}>
              Confusion Matrix
            </h2>
            <ConfusionMatrixTable cm={report.confusion_matrix} />
          </section>

          {/* Per-file results */}
          <section>
            <h2 style={{ fontSize: 16, fontWeight: 700, color: 'var(--ink)', marginBottom: 12 }}>
              Per-File Results
            </h2>
            {report.per_file.map((r, i) => (
              <PerFileRow key={i} r={r} />
            ))}
          </section>
        </>
      )}
    </div>
  );
}
