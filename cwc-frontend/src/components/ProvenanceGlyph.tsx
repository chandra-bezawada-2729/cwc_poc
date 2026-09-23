import type { ValueSource } from '../api/client';

/**
 * Where a value came from, as one character.
 *
 * The previous treatment printed "from document" in italics beside every
 * sender org and every fax number — thirty-four repetitions on a full Inbox,
 * which pushed real values into an ellipsis to make room for a phrase that
 * never changes. The information belongs on every row; the sentence does not.
 * One glyph, with the legend stated once above the table.
 */
export default function ProvenanceGlyph({ source }: { source: ValueSource | null | undefined }) {
  if (!source || source === 'NONE') return null;

  const fromDocument = source === 'EXTRACTION' || source === 'DOCUMENT';

  return (
    <i
      className={`glyph${fromDocument ? ' glyph-doc' : ''}`}
      title={fromDocument
        ? `Read from the page (${source.toLowerCase()})`
        : 'Parsed from the inbound file name — not read from the page'}
      aria-label={fromDocument ? 'read from the page' : 'parsed from the file name'}
    >
      {fromDocument ? 'D' : 'F'}
    </i>
  );
}
