interface Props {
  category: string | null;
  subtype?: string | null;
}

/** CONSULTATION_REPORT -> consultation-report, for the --cat-* token lookup. */
function slug(category: string): string {
  return category.toLowerCase().replace(/_/g, '-');
}

function titleCase(value: string): string {
  const words = value.replace(/_/g, ' ').toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * A category with its own colour, held in a token rather than picked per page.
 *
 * Folders are the thing staff navigate by, so a category should be recognisable
 * before it is read — the same hue on the Inbox row, the detail header and the
 * routing config table.
 */
export default function CategoryTag({ category, subtype }: Props) {
  if (!category) return <span className="em-dash">—</span>;

  return (
    <>
      <span className="cat-tag">
        <i style={{ background: `var(--cat-${slug(category)}, var(--cat-unknown))` }} />
        {titleCase(category)}
      </span>
      {subtype && <div className="cat-sub">{subtype.replace(/_/g, ' ').toLowerCase()}</div>}
    </>
  );
}
