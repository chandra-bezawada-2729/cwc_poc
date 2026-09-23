import { useEffect, useState } from 'react';

type Theme = 'dark' | 'light';
const KEY = 'cwc-theme';

/**
 * Dark is the default, light is the opt-out.
 *
 * The console is read for whole shifts in a records room, and the surrounding
 * light level is not something the app gets to know — so the choice stays with
 * the person and is remembered. localStorage can throw in a locked-down browser
 * profile, so every access is guarded and the app renders dark regardless.
 */
function read(): Theme {
  try {
    const saved = localStorage.getItem(KEY);
    if (saved === 'light' || saved === 'dark') return saved;
  } catch { /* private mode, blocked storage — fall through to the default */ }
  return 'dark';
}

export default function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(read);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem(KEY, theme); } catch { /* non-critical */ }
  }, [theme]);

  const next: Theme = theme === 'dark' ? 'light' : 'dark';

  return (
    <button
      type="button"
      className="theme-toggle"
      onClick={() => setTheme(next)}
      aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}
    >
      {theme === 'dark' ? (
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor"
             strokeWidth="1.4" aria-hidden="true">
          <circle cx="8" cy="8" r="3.2" />
          <path d="M8 1v1.6M8 13.4V15M15 8h-1.6M2.6 8H1M12.9 3.1l-1.1 1.1M4.2 11.8l-1.1 1.1M12.9 12.9l-1.1-1.1M4.2 4.2 3.1 3.1" />
        </svg>
      ) : (
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor"
             strokeWidth="1.4" aria-hidden="true">
          <path d="M13.5 9.8A6 6 0 0 1 6.2 2.5a6 6 0 1 0 7.3 7.3z" />
        </svg>
      )}
      {theme === 'dark' ? 'Light theme' : 'Dark theme'}
    </button>
  );
}
