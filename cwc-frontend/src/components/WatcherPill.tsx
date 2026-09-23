import { useEffect, useState } from 'react';
import { ingestApi, relativeTime, type IngestStatus } from '../api/client';

const POLL_MS = 10_000;

/**
 * Says, on screen, that the folder automation is alive.
 *
 * This is the single most useful thing the console can show and it was missing
 * entirely. A fax sitting motionless in the inbound folder looks identical
 * whether the scanner recognised it, never saw it, or is not running at all —
 * and working out which cost real debugging time. The path plus a last-scan
 * time answers it without opening a log.
 *
 * Three honest states, never a fourth optimistic one:
 *   reachable + enabled  — green, names the path and when it last scanned
 *   reachable + disabled — amber, says automation is off
 *   unreachable          — grey, says so rather than implying all is well
 */
export default function WatcherPill() {
  const [status, setStatus] = useState<IngestStatus | null>(null);
  const [unreachable, setUnreachable] = useState(false);
  // Re-render on a timer so "scanned 6s ago" keeps counting between polls
  // instead of freezing at whatever it said when the request landed.
  const [, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;

    const poll = () => {
      ingestApi.status()
        .then(s => { if (!cancelled) { setStatus(s); setUnreachable(false); } })
        .catch(() => { if (!cancelled) setUnreachable(true); });
    };

    poll();
    const statusTimer = setInterval(poll, POLL_MS);
    const clockTimer = setInterval(() => setTick(t => t + 1), 1000);

    return () => {
      cancelled = true;
      clearInterval(statusTimer);
      clearInterval(clockTimer);
    };
  }, []);

  if (unreachable || !status) {
    return (
      <span className="watcher watcher-idle" title="Could not reach the backend status endpoint">
        <span className="pulse" style={{ animation: 'none' }} />
        Status unavailable
      </span>
    );
  }

  if (!status.enabled) {
    return (
      <span
        className="watcher watcher-off"
        title="cwc.ingestion.inbound.enabled is false — faxes must be uploaded by hand"
      >
        <span className="pulse" style={{ animation: 'none' }} />
        Folder automation off
      </span>
    );
  }

  return (
    <span className="watcher" title={status.lastScanNote ?? undefined}>
      <span className="pulse" />
      Watching
      <span className="path">{status.inboundPath}</span>
      {status.lastScanAt && (
        <>
          <span className="sep">·</span>
          <span>scanned {relativeTime(status.lastScanAt)}</span>
        </>
      )}
    </span>
  );
}
