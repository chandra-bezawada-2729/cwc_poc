/**
 * Display filter for classification evidence quotes.
 *
 * The classification prompt already tells the model not to nominate letter
 * boilerplate for display, and `routingEvidence` / `namingEvidence` are curated
 * for exactly that reason. This is the backstop for when it does anyway — an
 * ophthalmology consult whose evidence reads "Dear Dr." and "Imp/Plan:" tells a
 * clinician nothing about why the fax was routed or named.
 *
 * Kept in one place on purpose: CWC will want to tune this list once they see
 * real output, and hunting inline regexes across components is not tuning.
 *
 * This is presentation only. It never touches the `evidence` array that the AI
 * service scores against the OCR text to compute calibrated confidence.
 */

/** Quotes matching any of these are dropped from display. */
export const BOILERPLATE_PATTERNS: readonly RegExp[] = [
  /^dear (dr|doctor)\.?$/i,
  /^(re|to|from|fax|phone|date|subject)\s*:?$/i,
  /^thank you (again )?for/i,
  /^this is an update/i,
  /^the following is a summary/i,
  /^please (contact|call|feel free)/i,
  /^sincerely/i,
  // A bare section label with nothing after the colon. "Imp/Plan:" is a heading;
  // the content under it is the evidence.
  /^[A-Za-z/ ]{1,20}:$/,
  /confidential|HIPAA|privileged|intended only for/i,
];

/** True when a quote is generic letter framing rather than clinical content. */
export function isBoilerplate(quote: string): boolean {
  const trimmed = quote.trim();
  if (!trimmed) return true;
  return BOILERPLATE_PATTERNS.some(p => p.test(trimmed));
}

/**
 * Drop boilerplate quotes, preserving order and de-duplicating.
 *
 * Returns [] when every quote was boilerplate — the caller decides whether to
 * hide the group or fall back to another list. Filtering to nothing is a real
 * answer here: an empty group is more honest than five useless quotes.
 */
export function filterEvidence(quotes: readonly string[] | null | undefined): string[] {
  if (!quotes) return [];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const q of quotes) {
    if (typeof q !== 'string') continue;
    const trimmed = q.trim();
    if (isBoilerplate(trimmed)) continue;
    const key = trimmed.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(trimmed);
  }
  return out;
}
