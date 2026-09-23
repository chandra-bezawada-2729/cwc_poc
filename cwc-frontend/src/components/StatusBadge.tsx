interface Props {
  status: string;
  errorReason?: string | null;
}

const CONFIG: Record<string, { label: string; bg: string; color: string }> = {
  RECEIVED:    { label: 'Received',    bg: 'var(--info-soft)', color: 'var(--info)' },
  VALIDATING:  { label: 'Validating', bg: 'var(--warn-soft)', color: 'var(--warn)' },
  OCR:         { label: 'OCR',        bg: 'var(--warn-soft)', color: 'var(--warn)' },
  CLASSIFYING: { label: 'Classifying',bg: 'var(--warn-soft)', color: 'var(--warn)' },
  CLASSIFIED:  { label: 'Classified', bg: 'var(--ok-soft)', color: 'var(--ok)' },
  ROUTED:      { label: 'Routed',     bg: 'var(--ok-soft)', color: 'var(--ok)' },
  ERRORED:     { label: 'Error',      bg: 'var(--crit-soft)', color: 'var(--crit)' },
};

export default function StatusBadge({ status, errorReason }: Props) {
  const cfg = CONFIG[status] ?? { label: status, bg: 'var(--surface-3)', color: 'var(--ink-2)' };
  return (
    <span
      title={errorReason ?? undefined}
      style={{
        display: 'inline-block',
        padding: '2px 10px',
        borderRadius: 9999,
        fontSize: 12,
        fontWeight: 600,
        background: cfg.bg,
        color: cfg.color,
        cursor: errorReason ? 'help' : 'default',
      }}
    >
      {cfg.label}
    </span>
  );
}
