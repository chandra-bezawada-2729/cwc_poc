import { useCallback, useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import {
  faxApi, metadataApi, type FaxDetail, type FaxMetadata,
  formatBytes, formatDateTime, relativeTime,
} from '../api/client';
import StatusBadge from '../components/StatusBadge';
import ProvenanceGlyph from '../components/ProvenanceGlyph';
import RoutingSuggestionCard from '../components/RoutingSuggestionCard';
import FeedbackWidget from '../components/FeedbackWidget';

/**
 * One label/value pair in a definition grid.
 *
 * Was a two-cell table row in a ~300px rail, which truncated the tracking ID,
 * the upload timestamp and the sender org mid-word. A grid with a fixed label
 * column and a wrapping value column shows the whole value instead of hiding
 * the end of it.
 */
function MetaRow(
  { label, value, wide }: { label: string; value: React.ReactNode; wide?: boolean },
) {
  return (
    <div className={`field lift-sm${wide ? ' field-wide' : ''}`}>
      <span className="field-label">{label}</span>
      <span className="field-value">{value ?? <span className="em-dash">—</span>}</span>
    </div>
  );
}

// ── Confidence badge for per-field metadata confidence ───────────────────────
function FieldConfBadge({ conf }: { conf: number | undefined }) {
  if (conf == null) return null;
  const pct = Math.round(conf * 100);
  const tone = conf >= 0.85 ? '' : conf >= 0.60 ? ' pip-med' : ' pip-low';
  return (
    <span className={`pip${tone}`} title="Extraction confidence for this field">
      {pct}%{conf < 0.60 ? ' ⚠' : ''}
    </span>
  );
}

// ── Extracted fields, inlined into the File Metadata card ────────────────────

/** "senderOrganization" → "Sender Organization", "icd10Codes" → "Icd10 Codes". */
function humanizeKey(key: string): string {
  const spaced = key.replace(/([a-z0-9])([A-Z])/g, '$1 $2');
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** Render a scalar or string-array extraction value; null for anything else. */
function displayValue(value: unknown): string | null {
  if (value == null || value === '') return null;
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (typeof value === 'string' || typeof value === 'number') return String(value);
  if (Array.isArray(value)) {
    const scalars = value.filter(v => typeof v === 'string' || typeof v === 'number');
    // Arrays of objects (test results, medications) are tabular — they belong on
    // the Metadata tab, not squeezed into a sidebar row.
    return scalars.length === value.length && scalars.length > 0 ? scalars.join(', ') : null;
  }
  return null;
}

const CORE_LABELS: Record<string, string> = {
  patientName:        'Patient name',
  patientDob:         'Patient DOB',
  patientMemberId:    'Member ID',
  patientMrn:         'MRN',
  prescriberName:     'Provider',
  prescriberNpi:      'Provider NPI',
  senderOrganization: 'Sender org',
  senderFax:          'Sender fax',
  responseFax:        'Response fax',
  responseDeadline:   'Response deadline',
  destinationFax:     'Received on fax',
  documentDate:       'Document date',
};

const EXTRACT_POLL_MS       = 3000;
const EXTRACT_POLL_ATTEMPTS = 25;   // ~75s — an extraction is a single vision call

/** A full-width note inside the field grid. */
function SpanRow({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="field field-wide"
      style={{ fontSize: 12, color: 'var(--ink-3)', background: 'transparent', borderStyle: 'dashed' }}
    >
      {children}
    </div>
  );
}

/**
 * The extracted fields, shown on the Classification & Routing tab as they become
 * available. Extraction starts automatically once a document reaches a resting
 * state, so this fills in on its own; the poll exists because the page is
 * usually open before the second vision call returns.
 *
 * Long free-text values are clamped rather than allowed to run past the card —
 * a FORM's "fields to complete" list is a paragraph, not a field.
 *
 * The Metadata tab remains the detailed view and keeps Re-Extract and Download.
 */
function useExtraction(trackingId: string, canExtract: boolean) {
  const [meta, setMeta]       = useState<FaxMetadata | null>(null);
  const [pending, setPending] = useState(true);

  useEffect(() => {
    let cancelled = false;
    let attempts  = 0;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const poll = () => {
      metadataApi.get(trackingId)
        .then(m => { if (!cancelled) { setMeta(m); setPending(false); } })
        .catch(() => {
          if (cancelled) return;
          attempts += 1;
          // Give up quietly. A document that was never filed has nothing to
          // extract, and an extraction that failed logged a warning server-side
          // — Re-Extract on the Metadata tab is the retry path.
          if (attempts >= EXTRACT_POLL_ATTEMPTS || !canExtract) {
            setPending(false);
            return;
          }
          timer = setTimeout(poll, EXTRACT_POLL_MS);
        });
    };
    poll();

    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, [trackingId, canExtract]);

  return { meta, pending };
}

function ExtractedFields({ meta, pending }: { meta: FaxMetadata | null; pending: boolean }) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  if (!meta) {
    return <SpanRow>{pending ? 'Extracting…' : 'No extracted metadata yet.'}</SpanRow>;
  }

  const conf = meta.extraction?.fieldConfidences ?? {};
  const core = (meta.core ?? {}) as unknown as Record<string, unknown>;
  const catD = (meta.categoryData ?? {}) as Record<string, unknown>;

  const rows: React.ReactNode[] = [];

  const push = (key: string, label: string, shown: string, confKey: string) => {
    // Anything past roughly a line and a half is prose, and prose in a value
    // column is what pushed the old card's text off the edge of the panel.
    const long = shown.length > 90;
    const open = expanded[key] ?? false;
    rows.push(
      <MetaRow
        key={key}
        label={label}
        wide={long}
        value={
          <>
            <span className={long && !open ? 'clamp-3' : undefined}>{shown}</span>
            <FieldConfBadge conf={conf[confKey]} />
            {long && (
              <button
                type="button"
                className="linkbtn"
                onClick={() => setExpanded(e => ({ ...e, [key]: !open }))}
              >
                {open ? 'Show less' : 'Show more'}
              </button>
            )}
          </>
        }
      />,
    );
  };

  for (const [key, label] of Object.entries(CORE_LABELS)) {
    const shown = displayValue(core[key]);
    if (shown == null) continue;
    push(`core.${key}`, label, shown, `core.${key}`);
  }

  for (const [key, value] of Object.entries(catD)) {
    const shown = displayValue(value);
    if (shown == null) continue;
    push(`categoryData.${key}`, humanizeKey(key), shown, `categoryData.${key}`);
  }

  if (rows.length === 0) {
    return <SpanRow>Extraction returned no populated fields.</SpanRow>;
  }

  return (
    <>
      {rows}
      <SpanRow>
        <span className="mono" style={{ fontSize: 11 }}>
          {meta.extraction?.promptVersion ?? 'schema unknown'}
        </span>
      </SpanRow>
    </>
  );
}

/**
 * Who and where, before anything else.
 *
 * Four fields answer the question a records clerk opens a fax to settle —
 * which facility sent it, which provider it concerns, which patient, and which
 * chart. Everything below this card is about how the system decided; this card
 * is about the document. It sits first for that reason.
 *
 * Patient name and MRN appear here and nowhere on the Inbox. The list view is
 * read over shoulders and pasted into screenshots; a detail page is a
 * deliberate click by someone already working that fax.
 */
function PriorityCard(
  { fax, meta, pending }:
  { fax: FaxDetail; meta: FaxMetadata | null; pending: boolean },
) {
  const core = (meta?.core ?? {}) as unknown as Record<string, unknown>;
  const conf = meta?.extraction?.fieldConfidences ?? {};

  const text = (v: unknown): string | null =>
    typeof v === 'string' && v.trim() ? v.trim()
      : typeof v === 'number' ? String(v)
        : null;

  // Facility falls back to the classifier's letterhead read, which lands with
  // the classification rather than waiting on the second vision call — so the
  // card is populated before extraction returns, not after.
  const facility = text(core.senderOrganization) ?? fax.senderOrganization;
  const facilityFromExtraction = text(core.senderOrganization) != null;

  const entries: Array<{ label: string; value: string | null; confKey?: string }> = [
    { label: 'Facility',     value: facility, confKey: facilityFromExtraction ? 'core.senderOrganization' : undefined },
    { label: 'Provider',     value: text(core.prescriberName), confKey: 'core.prescriberName' },
    { label: 'Patient name', value: text(core.patientName),    confKey: 'core.patientName' },
    { label: 'MRN',          value: text(core.patientMrn),     confKey: 'core.patientMrn' },
  ];

  const anyValue = entries.some(e => e.value);

  return (
    <div className="card lift">
      <h3 className="card-label">
        Patient &amp; facility
        {!anyValue && pending && <span className="qualifier">extracting…</span>}
      </h3>
      <div className="fieldgrid">
        {entries.map(e => (
          <div key={e.label} className="field field-priority lift-sm">
            <span className="field-label">{e.label}</span>
            <span className="field-value">
              {e.value ?? <span className="em-dash">{pending ? '…' : '—'}</span>}
              {e.value && e.confKey && <FieldConfBadge conf={conf[e.confKey]} />}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Metadata panel ────────────────────────────────────────────────────────────
function MetadataPanel({ trackingId, category }: { trackingId: string; category: string | null }) {
  const [meta, setMeta]         = useState<FaxMetadata | null>(null);
  const [loading, setLoading]   = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [error, setError]       = useState<string | null>(null);
  const [extracting, setExtracting] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    metadataApi.get(trackingId)
      .then(m => { setMeta(m); setNotFound(false); })
      .catch((e: unknown) => {
        if (e instanceof Error && e.message === 'NOT_FOUND') setNotFound(true);
        else setError(e instanceof Error ? e.message : 'Failed to load metadata');
      })
      .finally(() => setLoading(false));
  }, [trackingId]);

  useEffect(() => { load(); }, [load]);

  const triggerExtract = () => {
    setExtracting(true);
    metadataApi.extract(trackingId)
      .then(() => {
        // Poll every 3s until result appears
        const interval = setInterval(() => {
          metadataApi.get(trackingId)
            .then(m => { setMeta(m); setNotFound(false); clearInterval(interval); setExtracting(false); })
            .catch(() => { /* still waiting */ });
        }, 3000);
        setTimeout(() => { clearInterval(interval); setExtracting(false); }, 120_000);
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : 'Extract failed');
        setExtracting(false);
      });
  };

  if (loading) return <p style={{ color: 'var(--ink-3)', padding: 12 }}>Loading metadata…</p>;
  if (error)   return <p style={{ color: 'var(--crit)', padding: 12 }}>{error}</p>;

  if (notFound || !meta) {
    return (
      <div style={{ padding: 16 }}>
        <p style={{ color: 'var(--ink-3)', fontSize: 13, margin: '0 0 12px' }}>
          No extraction result yet. Click Extract to run category-specific field extraction.
        </p>
        <button
          onClick={triggerExtract}
          disabled={extracting}
          style={{
            padding: '7px 14px', borderRadius: 6, border: 'none',
            background: extracting ? 'var(--ink-3)' : 'var(--accent)', color: '#04211F',
            fontSize: 13, fontWeight: 600, cursor: extracting ? 'default' : 'pointer',
          }}
        >
          {extracting ? 'Extracting…' : 'Extract Metadata'}
        </button>
      </div>
    );
  }

  const conf  = meta.extraction?.fieldConfidences ?? {};
  const evid  = meta.extraction?.fieldEvidence ?? {};
  const core  = meta.core ?? {};
  const catD  = meta.categoryData ?? {};

  const coreRows: [string, string, keyof typeof core][] = [
    ['Patient Name',    'core.patientName',     'patientName'],
    ['Date of Birth',   'core.patientDob',      'patientDob'],
    ['Member ID',       'core.patientMemberId', 'patientMemberId'],
    ['MRN',             'core.patientMrn',      'patientMrn'],
    ['Prescriber',      'core.prescriberName',  'prescriberName'],
    ['Prescriber NPI',  'core.prescriberNpi',   'prescriberNpi'],
    ['Prescriber DEA',  'core.prescriberDea',   'prescriberDea'],
    ['Prescriber Phone','core.prescriberPhone', 'prescriberPhone'],
    ['Sender Org',      'core.senderOrganization','senderOrganization'],
    ['Sender Fax',      'core.senderFax',       'senderFax'],
    ['Response Fax',    'core.responseFax',     'responseFax'],
    ['Response Deadline','core.responseDeadline','responseDeadline'],
    ['Document Date',   'core.documentDate',    'documentDate'],
  ];

  const cardStyle: React.CSSProperties = {
    background: 'var(--surface)', borderRadius: 10,
    border: '1px solid var(--line)', overflow: 'hidden', marginBottom: 16,
  };
  const headerStyle: React.CSSProperties = {
    padding: '12px 16px', borderBottom: '1px solid var(--line)',
    fontWeight: 600, fontSize: 14, color: 'var(--ink-2)',
    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
  };

  return (
    <div>
      {/* Core fields */}
      <div style={cardStyle}>
        <div style={headerStyle}>
          <span>Patient &amp; Sender</span>
          <span style={{ fontSize: 11, color: 'var(--ink-3)', fontWeight: 400 }}>
            v{meta.extraction?.promptVersion ?? '—'}
          </span>
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <tbody>
            {coreRows.map(([label, key, field]) => {
              const val = core[field];
              if (val == null) return null;
              return (
                <tr key={key}>
                  <td style={{ padding: '7px 12px', fontWeight: 600, color: 'var(--ink-2)', fontSize: 12, whiteSpace: 'nowrap', width: 130 }}>
                    {label}
                  </td>
                  <td style={{ padding: '7px 12px', color: 'var(--ink)', fontSize: 13 }}>
                    {String(val)}
                    <FieldConfBadge conf={conf[key]} />
                    {evid[key] && (
                      <span style={{ display: 'block', fontSize: 11, color: 'var(--ink-3)', marginTop: 1 }}>
                        {evid[key]}
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Category-specific fields */}
      {Object.keys(catD).length > 0 && (
        <div style={cardStyle}>
          <div style={headerStyle}>
            <span>{category ?? 'Category'} Fields</span>
          </div>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <tbody>
              {Object.entries(catD).map(([k, v]) => {
                if (v == null) return null;
                const confKey = `categoryData.${k}`;
                const display = typeof v === 'object'
                  ? <pre style={{ margin: 0, fontSize: 11, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{JSON.stringify(v, null, 2)}</pre>
                  : String(v);
                return (
                  <tr key={k}>
                    <td style={{ padding: '7px 12px', fontWeight: 600, color: 'var(--ink-2)', fontSize: 12, whiteSpace: 'nowrap', width: 160, verticalAlign: 'top' }}>
                      {k}
                    </td>
                    <td style={{ padding: '7px 12px', color: 'var(--ink)', fontSize: 13, verticalAlign: 'top' }}>
                      {display}
                      <FieldConfBadge conf={conf[confKey]} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Download button */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
        <a
          href={metadataApi.downloadUrl(trackingId)}
          download={`${trackingId}-metadata.json`}
          style={{
            display: 'inline-block', padding: '7px 14px', borderRadius: 6,
            background: 'var(--accent)', color: '#04211F', fontSize: 13, fontWeight: 600,
            textDecoration: 'none',
          }}
        >
          Download JSON
        </a>
        <button
          onClick={() => { setMeta(null); setNotFound(false); setLoading(true); triggerExtract(); }}
          style={{
            padding: '7px 14px', borderRadius: 6, border: '1px solid var(--line)',
            background: 'var(--surface)', color: 'var(--ink-2)', fontSize: 13, cursor: 'pointer',
          }}
        >
          Re-Extract
        </button>
        <span style={{ fontSize: 11, color: 'var(--ink-3)' }}>
          Last run: {formatDateTime(meta.updatedAt)}
        </span>
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────
type Tab = 'details' | 'metadata';

export default function FaxDetailPage() {
  const { trackingId } = useParams<{ trackingId: string }>();
  const [fax, setFax]     = useState<FaxDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab]     = useState<Tab>('details');

  const load = useCallback(() => {
    if (!trackingId) return;
    faxApi.get(trackingId)
      .then(setFax)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : 'Failed to load'));
  }, [trackingId]);

  useEffect(() => { load(); }, [load]);

  // Called before the early returns below, because hooks cannot run
  // conditionally. It reads processingStatus defensively for the same reason:
  // on the first render fax is still null.
  const extraction = useExtraction(
    trackingId ?? '',
    fax?.processingStatus === 'CLASSIFIED' || fax?.processingStatus === 'ROUTED',
  );

  if (error) {
    return (
      <div>
        <Link to="/inbox" style={{ color: 'var(--accent)', textDecoration: 'none', fontSize: 14 }}>
          Back to Inbox
        </Link>
        <div style={{
          marginTop: 16, padding: '16px', borderRadius: 8,
          background: 'var(--crit-soft)', border: '1px solid color-mix(in srgb, var(--crit) 45%, transparent)', color: 'var(--crit)',
        }}>
          {error}
        </div>
      </div>
    );
  }

  if (!fax) return <p style={{ color: 'var(--ink-3)' }}>Loading…</p>;

  const fileUrl     = faxApi.fileUrl(trackingId!);
  const showRouting = fax.category != null;
  const canExtract  = fax.processingStatus === 'CLASSIFIED' || fax.processingStatus === 'ROUTED';

  return (
    <div>
      <div className="page-header">
        <div className="page-header-text">
          {/* The new name leads. The inbound name is provenance, and showing it
              first made the rename — the thing CWC actually files by — look
              like an afterthought. */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 9, flexWrap: 'wrap',
                        fontSize: 12.5, color: 'var(--ink-3)' }}>
            <Link to="/inbox" style={{ color: 'var(--accent)', textDecoration: 'none' }}>
              Inbox
            </Link>
            <span>/</span>
            <span className="mono" style={{ color: 'var(--ink)', fontWeight: 500, fontSize: 13 }}>
              {fax.finalFileName ?? fax.suggestedFileName ?? fax.originalFileName}
            </span>
          </div>
          <p className="page-subtitle">
            {fax.originalFileName !== (fax.finalFileName ?? fax.suggestedFileName) && (
              <>arrived as <span className="mono">{fax.originalFileName}</span> · </>
            )}
            {relativeTime(fax.createdAt)}
            {fax.uploadSource === 'FOLDER_SCAN' ? ' · from the watched folder' : ' · uploaded by hand'}
          </p>
        </div>
        {/* The verdict sits with the status, not at the foot of the page: it is
            asked about the decision shown here, and at the bottom it was below
            the fold on every fax with more than a couple of evidence lines. */}
        <div className="page-header-aside detail-header-aside">
          <FeedbackWidget trackingId={trackingId!} compact />
          <StatusBadge status={fax.processingStatus} errorReason={fax.errorReason} />
        </div>
      </div>

      <div className="tabbar">
        <button
          type="button"
          className={`tabbtn${tab === 'details' ? ' active' : ''}`}
          onClick={() => setTab('details')}
        >
          Classification &amp; Routing
        </button>
        <button
          type="button"
          className={`tabbtn${tab === 'metadata' ? ' active' : ''}`}
          onClick={() => setTab('metadata')}
          disabled={!canExtract && tab !== 'metadata'}
        >
          Metadata
          {!canExtract && (
            <span style={{ fontSize: 10, color: 'var(--ink-3)', marginLeft: 5 }}>
              (classify first)
            </span>
          )}
        </button>
      </div>

      {tab === 'details' && (
        <div className="detail-layout">

          <div className="detail-viewer">
            <div className="viewer-frame">
              <embed src={fileUrl} type="application/pdf" />
            </div>
            <a
              href={fileUrl}
              target="_blank"
              rel="noopener noreferrer"
              style={{ display: 'inline-block', marginTop: 8, fontSize: 12.5, color: 'var(--accent)' }}
            >
              Open full PDF
            </a>
          </div>

          {/* The rail answers, in order: what happened, why the folder, why the
              name, what it was filed as, and what the document says. Category,
              confidence and folder used to be restated across three cards. */}
          <div className="detail-rail">
            <PriorityCard fax={fax} meta={extraction.meta} pending={extraction.pending} />

            {showRouting && <RoutingSuggestionCard fax={fax} onConfirmed={load} />}

            <div className="card lift">
              <h3 className="card-label">Document</h3>
              <div className="fieldgrid">
                <MetaRow
                  label="Sender"
                  value={
                    fax.senderOrganization
                      ? <>{fax.senderOrganization}{' '}
                          <ProvenanceGlyph source={fax.senderOrganizationSource} /></>
                      : null
                  }
                />
                <MetaRow
                  label="Sender fax"
                  value={
                    fax.senderFaxNumber
                      ? <><span className="mono">{fax.senderFaxNumber}</span>{' '}
                          <ProvenanceGlyph source={fax.senderFaxNumberSource} /></>
                      : null
                  }
                />
                {fax.destinationFax && (
                  <MetaRow
                    label="Received on"
                    value={<span className="mono">{fax.destinationFax}</span>}
                  />
                )}
                <MetaRow label="Received" value={formatDateTime(fax.receivedAt)} />
                <MetaRow label="Pages" value={
                  fax.pageCount != null
                    ? `${fax.pageCount} · ${formatBytes(fax.fileSizeBytes)}`
                    : formatBytes(fax.fileSizeBytes)
                } />
                {fax.errorReason && (
                  <MetaRow
                    label="Error"
                    value={<span style={{ color: 'var(--crit)' }}>{fax.errorReason}</span>}
                  />
                )}

                {canExtract && (
                  <>
                    <div className="rule-label field-wide">Extracted</div>
                    <ExtractedFields meta={extraction.meta} pending={extraction.pending} />
                  </>
                )}

                <div className="rule-label field-wide">File</div>
                <MetaRow
                  label="Tracking ID"
                  value={<span className="mono" style={{ fontSize: 11.5 }}>{fax.trackingId}</span>}
                />
                <MetaRow label="Uploaded" value={formatDateTime(fax.createdAt)} />
                <MetaRow label="Type" value={fax.mimeType} />
                <MetaRow label="Source" value={fax.uploadSource} />
              </div>
            </div>
          </div>
        </div>
      )}

      {tab === 'metadata' && (
        <div className="detail-layout">
          <div className="detail-viewer">
            <div className="viewer-frame">
              <embed src={fileUrl} type="application/pdf" />
            </div>
          </div>
          <div className="detail-rail">
            {canExtract
              ? <MetadataPanel trackingId={trackingId!} category={fax.category} />
              : <p style={{ color: 'var(--ink-3)', fontSize: 13 }}>
                  Document must be CLASSIFIED or ROUTED before metadata can be extracted.
                </p>
            }
          </div>
        </div>
      )}

    </div>
  );
}
