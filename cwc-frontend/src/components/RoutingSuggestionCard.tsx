import { useEffect, useState } from 'react';
import type { ConfirmRequest, FaxDetail, RoutingConfig } from '../api/client';
import {
  faxApi, routingApi, foldersFromConfig, parseEvidence,
} from '../api/client';
import CategoryTag from './CategoryTag';
import ConfidenceMeter from './ConfidenceMeter';
import { filterEvidence } from '../utils/evidence';

interface Props {
  fax: FaxDetail;
  onConfirmed: () => void;
}

/**
 * Fallback folder list, used only if GET /api/routing/config fails. Kept in sync
 * with routing-config.yml v2.0. The live config is always preferred — CWC's
 * folder names are theirs, and a stale hardcoded list would offer folders that
 * do not exist on their SharePoint.
 */
const FALLBACK_FOLDERS = [
  'Consultation Reports',
  'Forms',
  'Homecare',
  'Hospital Records',
  'Lab Reports',
  'Manual-Review',
  'Miscellaneous',
  'Procedure Reports',
  'Radiology Reports',
  'ROI Consent Form',
];

function bandClass(band: string | null): string {
  if (band === 'LOW') return 'band band-low';
  if (band === 'MEDIUM') return 'band band-med';
  if (band === 'HIGH') return 'band band-high';
  return 'band';
}

/** One labelled group of evidence quotes. */
function EvidenceCard(
  { label, qualifier, quotes, naming, note }:
  { label: string; qualifier?: string; quotes: string[]; naming?: boolean; note?: string },
) {
  if (quotes.length === 0) return null;
  return (
    <div className="card lift">
      <h3 className="card-label">
        {label}
        {qualifier && <span className="qualifier">{qualifier}</span>}
      </h3>
      <ul className={`quotes${naming ? ' quotes-naming' : ''}`}>
        {quotes.map((q, i) => <li key={i} className="lift-sm">{q}</li>)}
      </ul>
      {note && (
        <div className="quotes-note">
          <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor"
               strokeWidth="1.4" aria-hidden="true">
            <circle cx="8" cy="8" r="6" /><path d="M5.5 8h5" />
          </svg>
          {note}
        </div>
      )}
    </div>
  );
}

export default function RoutingSuggestionCard({ fax, onConfirmed }: Props) {
  const suggestedName = fax.suggestedFileName ?? fax.originalFileName;

  const [selectedFolder, setSelectedFolder] = useState<string>(fax.suggestedFolder ?? '');
  const [fileName, setFileName]             = useState<string>(suggestedName);
  const [overrideReason, setOverrideReason] = useState('');
  const [confirming, setConfirming]         = useState(false);
  const [error, setError]                   = useState<string | null>(null);
  const [folders, setFolders]               = useState<string[]>(FALLBACK_FOLDERS);

  // Folder choices come from the live routing config so the dropdown can never
  // drift from what the routing engine will actually accept.
  useEffect(() => {
    let cancelled = false;
    routingApi.getConfig()
      .then((cfg: RoutingConfig) => { if (!cancelled) setFolders(foldersFromConfig(cfg)); })
      .catch(() => { /* keep the fallback list */ });
    return () => { cancelled = true; };
  }, []);

  // Re-seed when a different fax is selected.
  useEffect(() => {
    setSelectedFolder(fax.suggestedFolder ?? '');
    setFileName(fax.suggestedFileName ?? fax.originalFileName);
    setOverrideReason('');
    setError(null);
  }, [fax.trackingId, fax.suggestedFolder, fax.suggestedFileName, fax.originalFileName]);

  const isConfirmed = fax.routingStatus === 'CONFIRMED' || fax.routingStatus === 'OVERRIDDEN';

  // Folder and name overrides are tracked separately: a folder change means the
  // taxonomy was wrong, a name change means the specification was wrong.
  const folderOverride = selectedFolder !== fax.suggestedFolder;
  const nameOverride   = fileName.trim() !== suggestedName;
  const isOverride     = folderOverride || nameOverride;

  // Two questions, two answers: which folder, and which name. The flat list is
  // the fallback for rows classified before prompt v2.1 split them, which have
  // no subsets to show and would otherwise render nothing.
  const allEvidence     = parseEvidence(fax.evidence);
  const routingEvidence = filterEvidence(parseEvidence(fax.routingEvidence));
  const namingEvidence  = filterEvidence(parseEvidence(fax.namingEvidence));
  const hasSplitEvidence = routingEvidence.length > 0 || namingEvidence.length > 0;
  const flatEvidence = hasSplitEvidence ? [] : filterEvidence(parseEvidence(fax.evidence));

  // Being straight about what is not shown. The hidden quotes are real and they
  // still move calibrated confidence — implying they were discarded would be
  // a more comfortable lie than the one the numbers tell.
  const shownCount = routingEvidence.length + namingEvidence.length;
  const hiddenNote = hasSplitEvidence && allEvidence.length > shownCount
    ? `${allEvidence.length - shownCount} further quotes not shown — still counted toward confidence`
    : undefined;

  const handleConfirm = async () => {
    if (!selectedFolder) { setError('Select a folder'); return; }
    if (!fileName.trim()) { setError('Filename cannot be empty'); return; }
    if (isOverride && !overrideReason.trim()) {
      setError('Override reason is required when changing the suggested folder or filename');
      return;
    }
    setError(null);
    setConfirming(true);
    try {
      const req: ConfirmRequest = { folder: selectedFolder, fileName: fileName.trim() };
      if (isOverride) {
        req.overrideReason = overrideReason.trim();
        req.decidedBy = 'MANUAL';
      }
      await faxApi.confirm(fax.trackingId, req);
      onConfirmed();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Confirm failed');
    } finally {
      setConfirming(false);
    }
  };

  return (
    <>
      {/* ── Decision ──────────────────────────────────────────────────────
          One card answers "what happened to this fax". Category, subtype,
          confidence, band and folder used to be spread across three cards and
          restated in each; a reader had to reconcile them to trust any of it. */}
      <div className="card lift">
        <h3 className="card-label">Decision</h3>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 17, fontWeight: 600, letterSpacing: '-.015em' }}>
            <CategoryTag category={fax.category} />
          </span>
          {fax.subtype && <span className="chip">{fax.subtype}</span>}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 12, flexWrap: 'wrap' }}>
          <ConfidenceMeter score={fax.confidenceScore} band={fax.confidenceBand} size="lg" />
          {fax.confidenceBand && <span className={bandClass(fax.confidenceBand)}>{fax.confidenceBand}</span>}
          <span style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>calibrated</span>
        </div>

        {fax.forceManualReview && (
          <div style={{ marginTop: 10, fontSize: 12.5, color: 'var(--warn)' }}>
            Held for a human{fax.overrideReason ? ` — ${fax.overrideReason}` : ''}
          </div>
        )}

        {isConfirmed && fax.finalFolder && (
          <div style={{
            marginTop: 13, paddingTop: 13, borderTop: '1px solid var(--line-soft)',
            display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', fontSize: 12.5,
          }}>
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="var(--ok)"
                 strokeWidth="1.6" aria-hidden="true"><path d="M3 8.5l3.2 3.2L13 5" /></svg>
            Filed to <span className="mono">{fax.finalFolder}</span>
            {fax.routingStatus === 'OVERRIDDEN' && (
              <span style={{ color: 'var(--warn)' }}>
                (override{fax.fileNameOverridden ? ', name changed' : ''})
              </span>
            )}
          </div>
        )}
      </div>

      {/* ── Action required ───────────────────────────────────────────────── */}
      {fax.actionRequired && (
        <div className="card callout callout-warn lift">
          <h4>Action required</h4>
          <p>
            {fax.actionSummary ?? 'This fax needs a response.'}
            {fax.responseDeadline && <strong> By {fax.responseDeadline}.</strong>}
          </p>
        </div>
      )}

      {/* ── Why ───────────────────────────────────────────────────────────
          Two questions, two cards. The folder and the name are separate
          decisions and a single bullet list forced the reader to work out
          which quote was answering which. */}
      <EvidenceCard
        label="Why this folder"
        quotes={routingEvidence}
        note={hiddenNote}
      />
      <EvidenceCard
        label={fax.specification ? `Why it's called "${fax.specification}"` : 'Why this name'}
        quotes={namingEvidence}
        naming
      />
      <EvidenceCard
        label="Evidence"
        qualifier="classified before v2.1"
        quotes={flatEvidence}
      />

      {/* ── Filing ────────────────────────────────────────────────────────── */}
      <div className="card lift">
        <h3 className="card-label">
          Filing
          {fax.ruleMatched && <span className="qualifier">{fax.ruleMatched}</span>}
        </h3>

        <div className="renamebox lift-sm">
          <span className="from">{fax.originalFileName}</span>
          <span className="to">{isConfirmed ? (fax.finalFileName ?? suggestedName) : suggestedName}</span>
          {fax.alternateFileName && fax.alternateFileName !== suggestedName && (
            <span className="alt">
              Alternate convention · <code>{fax.alternateFileName}</code>
            </span>
          )}
          {fax.namingSource === 'FALLBACK' && (
            <span className="alt" style={{ color: 'var(--warn)' }}>
              Name defaulted — the classifier did not identify a specification.
            </span>
          )}
        </div>

        {!isConfirmed && (
          <div style={{ marginTop: 14, display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div>
              <label
                htmlFor="route-folder"
                style={{ display: 'block', fontSize: 11.5, color: 'var(--ink-3)', marginBottom: 5 }}
              >
                File to folder
              </label>
              <select
                id="route-folder"
                value={selectedFolder}
                onChange={e => setSelectedFolder(e.target.value)}
                style={{
                  width: '100%', padding: '8px 10px', borderRadius: 'var(--radius-sm)',
                  border: `1px solid ${folderOverride ? 'var(--warn)' : 'var(--line)'}`,
                  fontSize: 13, background: 'var(--surface)', color: 'var(--ink)',
                  font: 'inherit', fontFamily: 'var(--font-ui)',
                }}
              >
                <option value="">— choose folder —</option>
                {folders.map(f => <option key={f} value={f}>{f}</option>)}
              </select>
            </div>

            <div>
              <label
                htmlFor="route-filename"
                style={{ display: 'block', fontSize: 11.5, color: 'var(--ink-3)', marginBottom: 5 }}
              >
                File as
              </label>
              <input
                id="route-filename"
                type="text"
                value={fileName}
                onChange={e => setFileName(e.target.value)}
                spellCheck={false}
                style={{
                  width: '100%', padding: '8px 10px', borderRadius: 'var(--radius-sm)',
                  border: `1px solid ${nameOverride ? 'var(--warn)' : 'var(--line)'}`,
                  fontSize: 13, fontFamily: 'var(--font-mono)',
                  background: 'var(--surface)', color: 'var(--ink)',
                }}
              />
              {fax.alternateFileName && fileName !== fax.alternateFileName && (
                <button
                  type="button"
                  className="linkbtn"
                  onClick={() => setFileName(fax.alternateFileName!)}
                >
                  use {fax.alternateFileName}
                </button>
              )}
            </div>

            {isOverride && (
              <div>
                <label
                  htmlFor="route-override"
                  style={{ display: 'block', fontSize: 11.5, color: 'var(--warn)', marginBottom: 5 }}
                >
                  Override reason (required) —{' '}
                  {folderOverride && nameOverride
                    ? 'folder and filename changed'
                    : folderOverride ? 'folder changed' : 'filename changed'}
                </label>
                <input
                  id="route-override"
                  type="text"
                  value={overrideReason}
                  onChange={e => setOverrideReason(e.target.value)}
                  placeholder="What did the suggestion get wrong?"
                  style={{
                    width: '100%', padding: '8px 10px', borderRadius: 'var(--radius-sm)',
                    border: '1px solid var(--warn)', fontSize: 13,
                    background: 'var(--surface)', color: 'var(--ink)',
                    font: 'inherit', fontFamily: 'var(--font-ui)',
                  }}
                />
              </div>
            )}

            {error && (
              <div style={{ fontSize: 12, color: 'var(--crit)' }}>{error}</div>
            )}

            <button
              type="button"
              className="btn btn-primary"
              onClick={handleConfirm}
              disabled={confirming || !selectedFolder}
              style={{ width: '100%', padding: '9px 20px' }}
            >
              {confirming ? 'Confirming…' : 'Confirm routing'}
            </button>
          </div>
        )}
      </div>
    </>
  );
}
