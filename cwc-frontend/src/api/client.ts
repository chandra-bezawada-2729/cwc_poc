const BASE = '/api';

// ── Types ────────────────────────────────────────────────────────────────────

/**
 * Where a resolved value came from. EXTRACTION and DOCUMENT are both read off
 * the document itself; FILENAME means it was inferred from what the fax server
 * named the file, which is worth strictly less and is labelled as such.
 */
export type ValueSource = 'EXTRACTION' | 'DOCUMENT' | 'FILENAME' | 'NONE';

export interface FaxSummary {
  trackingId: string;
  originalFileName: string;
  senderFaxNumber: string | null;
  senderFaxNumberSource: ValueSource | null;
  senderOrganization: string | null;
  senderOrganizationSource: ValueSource | null;
  /** Which CWC line the fax arrived on. Only extraction produces it. */
  destinationFax: string | null;
  destinationFaxSource: ValueSource | null;
  receivedAt: string | null;
  processingStatus: string;
  uploadSource: string;
  fileSizeBytes: number;
  pageCount: number | null;
  mimeType: string | null;
  errorReason: string | null;
  createdAt: string;
  updatedAt: string;
  // Classification summary (null until CLASSIFIED)
  category: string | null;
  subtype: string | null;
  confidenceBand: string | null;
  /**
   * 0..1. The Inbox shows a meter rather than a band word: every row reads HIGH,
   * so the badge alone stopped distinguishing 82% from 97% - which is exactly the
   * difference that decides whether someone checks the work.
   */
  calibratedConfidence: number | null;
  forceManualReview: boolean | null;
  // Routing summary (null until suggestion computed)
  suggestedFolder: string | null;
  routingStatus: string | null;
  /** CWC house style, e.g. "US Abdomen.pdf" */
  suggestedFileName: string | null;
  /** specification_doctype style, e.g. "us_abdomen_radiologyreport.pdf" */
  alternateFileName: string | null;
}

export interface FaxDetail extends FaxSummary {
  confidenceScore: number | null;
  actionRequired: boolean | null;
  actionSummary: string | null;
  responseDeadline: string | null;
  /** Return fax read from the document body, as opposed to the filename parse. */
  senderCallbackFax: string | null;
  /** JSON array string of ALL verbatim evidence quotes. Feeds evidenceScore. */
  evidence: string | null;
  /** JSON array string — display subset: why this folder. Null before prompt v2.1. */
  routingEvidence: string | null;
  /** JSON array string — display subset: why this name. Null before prompt v2.1. */
  namingEvidence: string | null;
  /** UNKNOWN | AMBIGUOUS | UNVERIFIED_EVIDENCE | AUTO_ROUTE_DISABLED:X */
  overrideReason: string | null;
  // Rename detail (taxonomy v2.0)
  /** Short noun phrase: "US Abdomen", "Allergy", "Medical Clearance". */
  specification: string | null;
  /** MODEL when the classifier supplied a specification; FALLBACK when defaulted. */
  namingSource: string | null;
  /** Leading fax cover-sheet pages detected. */
  coverSheetPages: number | null;
  // Routing detail
  finalFolder: string | null;
  finalFileName: string | null;
  fileNameOverridden: boolean | null;
  ruleMatched: string | null;
}

export interface RoutingConfig {
  mode: string;
  manualReviewFolder: string;
  autoRouteDisabled: string[];
  /**
   * 0..1. Below this a document goes to Manual-Review instead of its predicted
   * folder. Separate from the confidence band on purpose: the band describes
   * the model, this is CWC's policy on how much doubt is acceptable.
   */
  autoRouteMinConfidence?: number;
  rules: Array<{ category: string; folder: string }>;
  subtypeOverrides: Array<{ category: string; subtype: string; folder: string }>;
  senderOverrides: Array<{ senderFax: string; folder: string }>;
  naming?: {
    style: string;
    collisionSuffix: string;
    preserveOriginalInAudit: boolean;
  };
}

/**
 * Every folder the routing config can file into, deduplicated and sorted.
 * The folder dropdown MUST be derived from the live config rather than a
 * hardcoded list — CWC's folder names changed wholesale in taxonomy v2.0, and a
 * hardcoded list silently offers folders that no longer exist.
 */
export function foldersFromConfig(cfg: RoutingConfig): string[] {
  const set = new Set<string>();
  cfg.rules.forEach(r => set.add(r.folder));
  cfg.subtypeOverrides.forEach(s => set.add(s.folder));
  cfg.senderOverrides.forEach(s => set.add(s.folder));
  if (cfg.manualReviewFolder) set.add(cfg.manualReviewFolder);
  return Array.from(set).sort((a, b) => a.localeCompare(b));
}

export interface ReviewQueuePage {
  content: FaxDetail[];
  totalElements: number;
}

export interface FaxPage {
  content: FaxSummary[];
  totalElements: number;
  totalPages: number;
  number: number;
  size: number;
}

export interface UploadAccepted {
  trackingId: string;
  originalFileName: string;
  status: string;
}

export interface UploadRejected {
  originalFileName: string;
  reason: string;
}

export interface UploadResponse {
  uploaded: UploadAccepted[];
  failed: UploadRejected[];
}

// ── Phase 9 Metadata types ───────────────────────────────────────────────────

export interface MetadataCore {
  patientName:        string | null;
  patientDob:         string | null;
  patientMemberId:    string | null;
  patientMrn:         string | null;
  prescriberName:     string | null;
  prescriberNpi:      string | null;
  prescriberDea:      string | null;
  prescriberPhone:    string | null;
  senderOrganization: string | null;
  senderFax:          string | null;
  responseFax:        string | null;
  responseDeadline:   string | null;
  /** The number the fax was sent TO — which CWC line it arrived on. */
  destinationFax:     string | null;
  documentDate:       string | null;
}

export interface MetadataExtraction {
  promptVersion:    string | null;
  modelName:        string | null;
  latencyMs:        number;
  fieldConfidences: Record<string, number>;
  fieldEvidence:    Record<string, string>;
  error?:           string;
}

export interface FaxMetadata {
  schemaVersion: string;
  trackingId:    string;
  category:      string | null;
  core:          MetadataCore;
  categoryData:  Record<string, unknown>;
  extraction:    MetadataExtraction;
  createdAt:     string;
  updatedAt:     string;
}

export interface ListParams {
  status?: string;
  q?: string;
  page?: number;
  size?: number;
}

export interface ConfirmRequest {
  folder: string;
  /** Omit to accept the suggested filename. */
  fileName?: string;
  overrideReason?: string;
  decidedBy?: string;
}

// ── Reviewer feedback ────────────────────────────────────────────────────────

export type Verdict = 'UP' | 'DOWN';

export interface Feedback {
  trackingId: string;
  verdict: Verdict;
  /** Only present for DOWN: what went wrong, in the reviewer's words. */
  summary: string | null;
  /** What the AI decided, as it stood when the feedback was given. */
  originalFileName: string | null;
  aiFileName: string | null;
  aiFolder: string | null;
  aiCategory: string | null;
  aiSubtype: string | null;
  aiConfidence: number | null;
  reviewState: string | null;
  createdAt: string | null;
  updatedAt: string | null;
}

export interface FeedbackList {
  total: number;
  upCount: number;
  downCount: number;
  items: Feedback[];
}

// ── Folder contents on the server ────────────────────────────────────────────

export interface StorageFile {
  name: string;
  sizeBytes: number;
  modifiedAt: string;
  /** Set when this file is one the application filed; null when it is not. */
  trackingId: string | null;
  originalFileName: string | null;
  category: string | null;
}

export interface StorageFolder {
  name: string;
  path: string;
  fileCount: number;
  totalBytes: number;
  files: StorageFile[];
  folders: StorageFolder[];
  truncated: boolean;
}

export interface StorageBrowse {
  basePath: string;
  generatedAt: string;
  roots: StorageFolder[];
  warnings: string[];
}

// ── API client ───────────────────────────────────────────────────────────────

export const faxApi = {
  async upload(files: File[]): Promise<UploadResponse> {
    const form = new FormData();
    files.forEach(f => form.append('files', f));
    const res = await fetch(`${BASE}/faxes/upload`, { method: 'POST', body: form });
    // 400 is the all-rejected case and its body is a normal UploadResponse —
    // every file was a duplicate, say so. Throwing here would replace the
    // per-file reason with a bare "Upload failed: 400", which is exactly the
    // situation the reasons were written for.
    if (res.status === 400) {
      const body = await res.json().catch(() => null);
      if (body && Array.isArray(body.failed)) return body as UploadResponse;
    }
    if (!res.ok) throw new Error(`Upload failed: ${res.status} ${res.statusText}`);
    return res.json();
  },

  async list(params: ListParams = {}): Promise<FaxPage> {
    const qs = new URLSearchParams();
    if (params.status) qs.set('status', params.status);
    if (params.q)      qs.set('q', params.q);
    qs.set('page', String(params.page ?? 0));
    qs.set('size', String(params.size ?? 20));
    const res = await fetch(`${BASE}/faxes?${qs}`);
    if (!res.ok) throw new Error(`Failed to load faxes: ${res.status}`);
    return res.json();
  },

  async get(trackingId: string): Promise<FaxDetail> {
    const res = await fetch(`${BASE}/faxes/${trackingId}`);
    if (!res.ok) throw new Error(`Failed to load fax: ${res.status}`);
    return res.json();
  },

  fileUrl(trackingId: string): string {
    return `${BASE}/faxes/${trackingId}/file`;
  },

  async confirm(trackingId: string, req: ConfirmRequest): Promise<unknown> {
    const res = await fetch(`${BASE}/faxes/${trackingId}/confirm`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req),
    });
    if (!res.ok) {
      const body = await res.text();
      throw new Error(body || `Confirm failed: ${res.status}`);
    }
    return res.json();
  },
};

export interface DashboardStats {
  byStatus:       Record<string, number>;
  byCategory:     Record<string, number>;
  byBand:         Record<string, number>;
  total:          number;
  autoRouteRate:  number;
  overrideRate:   number;
  totalRouted:    number;
  avgLatencyMs:   number | null;
  actionDueSoon:  number;
}

/**
 * Live state of the inbound-folder automation.
 *
 * <p>Backs the watcher indicator. Without it the UI can say a folder is
 * configured but not that the scanner is alive right now, and those are very
 * different answers when a file appears to be sitting still.
 */
export interface IngestStatus {
  enabled: boolean;
  inboundPath: string | null;
  archivePath: string | null;
  failedPath: string | null;
  byState: Record<string, number>;
  backlog: number;
  filed: number;
  quarantined: number;
  /** ISO instant of the last completed scan cycle. Null before the first one. */
  lastScanAt: string | null;
  /** What that cycle concluded, e.g. "ok" or "inbound path not available". */
  lastScanNote: string | null;
  /** Poll interval in ms, so the UI can say when the next scan is due. */
  pollIntervalMs: number | null;
}

export const ingestApi = {
  async status(): Promise<IngestStatus> {
    const res = await fetch(`${BASE}/ingest/status`);
    if (!res.ok) throw new Error(`Failed to load ingest status: ${res.status}`);
    return res.json();
  },
};

export const dashboardApi = {
  async stats(): Promise<DashboardStats> {
    const res = await fetch(`${BASE}/dashboard/stats`);
    if (!res.ok) throw new Error(`Failed to load stats: ${res.status}`);
    return res.json();
  },
};

export const routingApi = {
  async getConfig(): Promise<RoutingConfig> {
    const res = await fetch(`${BASE}/routing/config`);
    if (!res.ok) throw new Error(`Failed to load routing config: ${res.status}`);
    return res.json();
  },

  async reload(): Promise<{ status: string; mode: string }> {
    const res = await fetch(`${BASE}/routing/reload`, { method: 'POST' });
    if (!res.ok) throw new Error(`Reload failed: ${res.status}`);
    return res.json();
  },

  async setMode(mode: 'AUTO' | 'SUGGEST'): Promise<{ status: string; mode: string }> {
    const res = await fetch(`${BASE}/routing/mode`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode }),
    });
    if (!res.ok) throw new Error(`Mode change failed: ${res.status}`);
    return res.json();
  },
};

export const metadataApi = {
  async get(trackingId: string): Promise<FaxMetadata> {
    const res = await fetch(`${BASE}/faxes/${trackingId}/metadata`);
    if (!res.ok) {
      if (res.status === 404) throw new Error('NOT_FOUND');
      throw new Error(`Failed to load metadata: ${res.status}`);
    }
    return res.json();
  },

  downloadUrl(trackingId: string): string {
    return `${BASE}/faxes/${trackingId}/metadata/download`;
  },

  async extract(trackingId: string): Promise<{ trackingId: string; message: string }> {
    const res = await fetch(`${BASE}/faxes/${trackingId}/extract`, { method: 'POST' });
    if (!res.ok) {
      const body = await res.text();
      throw new Error(body || `Extract failed: ${res.status}`);
    }
    return res.json();
  },
};

export const reviewApi = {
  async queue(): Promise<ReviewQueuePage> {
    const res = await fetch(`${BASE}/review-queue`);
    if (!res.ok) throw new Error(`Failed to load review queue: ${res.status}`);
    return res.json();
  },
};

// ── Helpers ──────────────────────────────────────────────────────────────────

const TERMINAL_STATUSES = new Set(['CLASSIFIED', 'ROUTED', 'ERRORED']);

export function isTerminal(status: string): boolean {
  return TERMINAL_STATUSES.has(status);
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatDateTime(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('en-US', {
    month: 'short', day: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit', hour12: true,
  });
}

export function parseEvidence(evidence: string | null): string[] {
  if (!evidence) return [];
  try {
    const parsed = JSON.parse(evidence);
    return Array.isArray(parsed) ? parsed.map(String) : [];
  } catch {
    return [];
  }
}

/**
 * The provenance tag shown beside a resolved value.
 *
 * EXTRACTION and DOCUMENT collapse to the same label on purpose: a clinician
 * cares that the number was read off the page, not which pass read it. The
 * precise source stays available for a tooltip.
 */
export function sourceLabel(source: ValueSource | null | undefined): string | null {
  switch (source) {
    case 'EXTRACTION':
    case 'DOCUMENT':
      return 'from document';
    case 'FILENAME':
      return 'from filename';
    default:
      return null;
  }
}

/**
 * "6s ago", "4 min ago", "2 hr ago".
 *
 * An absolute timestamp answers "when"; on a live queue the question is
 * "is this current", and a relative figure answers that without arithmetic.
 * Falls back to the absolute form past a day, where relative stops helping.
 */
export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return '-';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '-';
  const secs = Math.round((Date.now() - then) / 1000);
  if (secs < 0) return 'just now';
  if (secs < 10) return 'just now';
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs} hr ago`;
  return formatDateTime(iso);
}

export function confidencePct(score: number | null): string {
  if (score == null) return '—';
  return `${Math.round(score * 100)}%`;
}

export const feedbackApi = {
  async get(trackingId: string): Promise<Feedback | null> {
    const res = await fetch(`${BASE}/faxes/${trackingId}/feedback`);
    // 204 means nobody has judged this fax yet, which is not an error.
    if (res.status === 204) return null;
    if (!res.ok) throw new Error(`Failed to load feedback: ${res.status}`);
    return res.json();
  },

  async submit(trackingId: string, verdict: Verdict, summary?: string): Promise<Feedback> {
    const res = await fetch(`${BASE}/faxes/${trackingId}/feedback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ verdict, summary: summary ?? null }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      throw new Error(body?.error ?? `Failed to save feedback: ${res.status}`);
    }
    return res.json();
  },

  async list(verdict?: Verdict): Promise<FeedbackList> {
    const qs = verdict ? `?verdict=${verdict}` : '';
    const res = await fetch(`${BASE}/feedback${qs}`);
    if (!res.ok) throw new Error(`Failed to load feedback: ${res.status}`);
    return res.json();
  },

  /** The browser downloads the file; nothing to parse here. */
  csvUrl(verdict?: Verdict): string {
    return `${BASE}/feedback/export.csv${verdict ? `?verdict=${verdict}` : ''}`;
  },
};

export const storageApi = {
  async browse(): Promise<StorageBrowse> {
    const res = await fetch(`${BASE}/storage/browse`);
    if (!res.ok) throw new Error(`Failed to read folders: ${res.status}`);
    return res.json();
  },
};
