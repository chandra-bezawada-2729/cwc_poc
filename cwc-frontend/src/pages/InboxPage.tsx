import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  faxApi,
  dashboardApi,
  ingestApi,
  type DashboardStats,
  type FaxSummary,
  type IngestStatus,
  type UploadResponse,
  isTerminal,
  relativeTime,
} from '../api/client';
import CategoryTag from '../components/CategoryTag';
import ConfidenceMeter from '../components/ConfidenceMeter';
import PipelineStrip from '../components/PipelineStrip';
import ReviewAlert from '../components/ReviewAlert';
import ProvenanceGlyph from '../components/ProvenanceGlyph';
import UploadDropzone from '../components/UploadDropzone';
import WatcherPill from '../components/WatcherPill';

const POLL_MS = 3000;

const EM_DASH = <span className="em-dash">—</span>;

function FolderIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor"
         strokeWidth="1.4" aria-hidden="true">
      <path d="M1.8 4.2A1 1 0 0 1 2.8 3.2h3l1.4 1.6h5a1 1 0 0 1 1 1v6.2a1 1 0 0 1-1 1H2.8a1 1 0 0 1-1-1z" />
    </svg>
  );
}

/**
 * Which colour the row's edge stripe takes.
 *
 * State used to be a pill in the seventh column, which costs width to repeat
 * what nearly every row says and cannot be spotted while scrolling. As a stripe
 * it is free, and an amber or red row is findable without reading anything.
 */
function rowTone(fax: FaxSummary): string {
  if (fax.processingStatus === 'ERRORED' || fax.routingStatus === 'FAILED') return 'row-crit';
  if (fax.forceManualReview || fax.routingStatus === 'MANUAL_REVIEW' || fax.confidenceBand === 'LOW') {
    return 'row-warn';
  }
  if (!isTerminal(fax.processingStatus)) return 'row-busy';
  if (fax.processingStatus === 'ROUTED') return 'row-ok';
  return '';
}

function stateTitle(fax: FaxSummary): string {
  if (fax.processingStatus === 'ERRORED') return fax.errorReason ?? 'Errored';
  if (fax.routingStatus === 'FAILED') return 'Filing failed';
  if (fax.forceManualReview) return 'Held for manual review';
  if (fax.routingStatus === 'MANUAL_REVIEW') return 'In review';
  if (fax.processingStatus === 'ROUTED') return 'Filed';
  return fax.processingStatus.toLowerCase();
}

export default function InboxPage() {
  const [faxes, setFaxes]         = useState<FaxSummary[]>([]);
  const [total, setTotal]         = useState(0);
  const [uploading, setUploading] = useState(false);
  const [uploadMsg, setUploadMsg] = useState<UploadResponse | null>(null);
  const [error, setError]         = useState<string | null>(null);
  const [loading, setLoading]     = useState(true);
  const [stats, setStats]         = useState<DashboardStats | null>(null);
  const [ingest, setIngest]       = useState<IngestStatus | null>(null);

  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const loadStats = useCallback(async () => {
    try { setStats(await dashboardApi.stats()); } catch { /* non-blocking */ }
  }, []);

  const loadIngest = useCallback(async () => {
    try { setIngest(await ingestApi.status()); } catch { /* automation may be off */ }
  }, []);

  const loadFaxes = useCallback(async () => {
    try {
      const page = await faxApi.list({ size: 50 });
      setFaxes(page.content);
      setTotal(page.totalElements);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load faxes');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadFaxes(); loadStats(); loadIngest(); }, [loadFaxes, loadStats, loadIngest]);

  useEffect(() => {
    const hasActive = faxes.some(f => !isTerminal(f.processingStatus));
    if (hasActive) {
      pollRef.current = setTimeout(() => { loadFaxes(); loadStats(); }, POLL_MS);
    }
    return () => { if (pollRef.current) clearTimeout(pollRef.current); };
  }, [faxes, loadFaxes, loadStats]);

  const handleFiles = async (files: File[]) => {
    setUploading(true);
    setUploadMsg(null);
    setError(null);
    try {
      const resp = await faxApi.upload(files);
      setUploadMsg(resp);
      await loadFaxes();
      await loadStats();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Upload failed');
    } finally {
      setUploading(false);
    }
  };

  const newest = faxes[0]?.createdAt ?? null;

  return (
    <div>
      <div className="page-header">
        <div className="page-header-text">
          <h1 className="page-title">Inbox</h1>
          <p className="page-subtitle">
            {total > 0
              ? `${total} fax${total !== 1 ? 'es' : ''}${newest ? ` · last arrival ${relativeTime(newest)}` : ''}`
              : 'All inbound faxes'}
          </p>
        </div>
        <div className="page-header-aside">
          <WatcherPill />
        </div>
      </div>

      <div className="stack">
        {/* Above the pipeline strip on purpose: work waiting on a person comes
            before a summary of work that already completed. */}
        <ReviewAlert refreshKey={faxes.length} />

        {stats && <PipelineStrip stats={stats} />}

        <UploadDropzone
          onFiles={handleFiles}
          uploading={uploading}
          inboundPath={ingest?.enabled ? ingest.inboundPath : null}
        />

        {uploadMsg && (
          <div className={`callout lift ${uploadMsg.failed.length > 0 ? 'callout-warn' : 'callout-ok'}`}>
            {uploadMsg.uploaded.length > 0 && (
              <h4>
                {uploadMsg.uploaded.length} file{uploadMsg.uploaded.length !== 1 ? 's' : ''} queued
                for processing
              </h4>
            )}
            {uploadMsg.failed.length > 0 && (
              /* One line per file, with its reason. A duplicate says where the
                 earlier copy was filed, which is the thing the person uploading
                 actually wants to know — a bare list of names does not answer it. */
              <>
                <h4>{uploadMsg.failed.length} rejected</h4>
                <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
                  {uploadMsg.failed.map((f, i) => (
                    <li key={i} style={{ fontSize: 12.5, color: 'var(--ink-2)', lineHeight: 1.55 }}>
                      <span className="mono">{f.originalFileName}</span>
                      {f.reason && <span> — {f.reason}</span>}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        )}

        {error && (
          <div className="callout callout-crit">
            <h4>Could not load the inbox</h4>
            <p>{error}</p>
          </div>
        )}

        {loading ? (
          <p style={{ color: 'var(--ink-3)' }}>Loading…</p>
        ) : faxes.length === 0 ? (
          <div className="placeholder-card">
            <h2>No faxes yet</h2>
            <p>
              {ingest?.enabled && ingest.inboundPath
                ? <>Drop a PDF into <span className="mono">{ingest.inboundPath}</span> and it will
                    appear here within a few seconds.</>
                : 'Upload a PDF above to get started.'}
            </p>
          </div>
        ) : (
          <div className="sheet">
            <div className="sheet-head">
              <h3>Recent</h3>
              <div className="legend">
                <span><i className="glyph glyph-doc">D</i> read from the page</span>
                <span><i className="glyph">F</i> parsed from the file name</span>
              </div>
            </div>

            <div className="sheet-scroll">
              {/* A grid list, not a table. Each fax needs to be its own box —
                  rounded, individually shadowed, and able to scale on hover —
                  and a <tr> can do none of those reliably. The column template
                  is shared with the header row so everything still lines up. */}
              <div className="wl" role="table" aria-label="Recent faxes">
                <div className="wl-cols wl-head" role="row">
                  <span role="columnheader">Document</span>
                  <span role="columnheader">Sender</span>
                  <span role="columnheader">Receiver fax</span>
                  <span role="columnheader">Type</span>
                  <span role="columnheader">Confidence</span>
                  <span role="columnheader">Filed to</span>
                  <span role="columnheader">Arrived</span>
                  <span role="columnheader"><span className="sr-only">Actions</span></span>
                </div>

                {faxes.map(fax => (
                  <div
                    key={fax.trackingId}
                    role="row"
                    className={`wl-row wl-cols lift ${rowTone(fax)}`}
                    title={stateTitle(fax)}
                  >
                    {/* Document — the rename leads, because the new name is the
                        output CWC cares about. The inbound name is provenance. */}
                    <div role="cell">
                      {fax.suggestedFileName ? (
                        <>
                          <span
                            className="fname"
                            title={fax.alternateFileName
                              ? `alternate: ${fax.alternateFileName}`
                              : fax.suggestedFileName}
                          >
                            {fax.suggestedFileName}
                          </span>
                          <span className="forig">
                            ↖ <span title={fax.originalFileName}>{fax.originalFileName}</span>
                          </span>
                        </>
                      ) : (
                        <span className="fname" title={fax.originalFileName}>
                          {fax.originalFileName}
                        </span>
                      )}
                    </div>

                    {/* Sender — org above, fax with its provenance below.
                        No patient identifiers here: a list view is read over
                        shoulders and in screenshots. Those stay on the detail page. */}
                    <div role="cell">
                      <div className="org">
                        <span title={fax.senderOrganization ?? undefined}>
                          {fax.senderOrganization ?? EM_DASH}
                        </span>
                        <ProvenanceGlyph source={fax.senderOrganizationSource} />
                      </div>
                      <div className="faxline">
                        <ProvenanceGlyph source={fax.senderFaxNumberSource} />
                        <span className="mono">{fax.senderFaxNumber ?? '—'}</span>
                      </div>
                    </div>

                    {/* Receiver fax — which CWC line the fax arrived on. Only
                        extraction produces it, so it is blank until the second
                        vision call returns rather than guessed from the file. */}
                    <div role="cell">
                      {fax.destinationFax
                        ? (
                          <span className="recvfax">
                            <ProvenanceGlyph source={fax.destinationFaxSource} />
                            <span className="mono">{fax.destinationFax}</span>
                          </span>
                        )
                        : EM_DASH}
                    </div>

                    <div role="cell">
                      {fax.category
                        ? <CategoryTag category={fax.category} subtype={fax.subtype} />
                        : <span className="cat-sub" style={{ marginTop: 0 }}>
                            {fax.processingStatus.toLowerCase()}
                          </span>}
                    </div>

                    <div role="cell">
                      <ConfidenceMeter
                        score={fax.calibratedConfidence}
                        band={fax.confidenceBand}
                      />
                      {fax.forceManualReview && (
                        <div className="cat-sub" style={{ color: 'var(--warn)' }}>held for review</div>
                      )}
                    </div>

                    <div role="cell">
                      {fax.suggestedFolder
                        ? <span className="folder-cell"><FolderIcon />{fax.suggestedFolder}</span>
                        : EM_DASH}
                    </div>

                    <div role="cell" className="when" title={fax.createdAt}>
                      {relativeTime(fax.receivedAt ?? fax.createdAt)}
                    </div>

                    <div role="cell">
                      <Link className="rowlink" to={`/faxes/${fax.trackingId}`}>Open →</Link>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {!loading && faxes.length > 0 && (
          <p style={{ fontSize: 12, color: 'var(--ink-3)' }}>
            {total} total · showing {faxes.length}
          </p>
        )}
      </div>
    </div>
  );
}
