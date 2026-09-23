import { useCallback, useEffect, useState } from 'react';
import { routingApi, type RoutingConfig } from '../api/client';


export default function RoutingConfigPage() {
  const [config, setConfig]     = useState<RoutingConfig | null>(null);
  const [loading, setLoading]   = useState(true);
  const [reloading, setReloading] = useState(false);
  const [error, setError]       = useState<string | null>(null);
  const [reloadMsg, setReloadMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const cfg = await routingApi.getConfig();
      setConfig(cfg);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load routing config');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleReload = async () => {
    setReloading(true);
    setReloadMsg(null);
    try {
      const result = await routingApi.reload();
      setReloadMsg(`Reloaded. Mode: ${result.mode}`);
      await load();
    } catch (e: unknown) {
      setReloadMsg(e instanceof Error ? `Reload failed: ${e.message}` : 'Reload failed');
    } finally {
      setReloading(false);
    }
  };

  const handleModeToggle = async () => {
    if (!config) return;
    const next = config.mode === 'AUTO' ? 'SUGGEST' : 'AUTO';
    setReloadMsg(null);
    try {
      await routingApi.setMode(next);
      setReloadMsg(`Mode set to ${next} (in memory only — edit routing-config.yml to persist)`);
      await load();
    } catch (e: unknown) {
      setReloadMsg(e instanceof Error ? `Mode change failed: ${e.message}` : 'Mode change failed');
    }
  };

  return (
    <div>
      <div className="page-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end' }}>
        <div>
          <h1 className="page-title">Routing Config</h1>
          <p className="page-subtitle">
            Read-only view of <code>routing-config.yml</code> — edit the file directly, then reload
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          {config && (
            <button
              onClick={handleModeToggle}
              title="Toggles in-memory only — does not edit routing-config.yml"
              style={{
                background: config.mode === 'AUTO' ? 'var(--ok-soft)' : 'var(--surface-3)',
                color: config.mode === 'AUTO' ? 'var(--ok)' : 'var(--ink-2)',
                border: `1px solid ${config.mode === 'AUTO' ? 'color-mix(in srgb, var(--ok) 45%, transparent)' : 'var(--line)'}`,
                borderRadius: 6, padding: '9px 16px', fontSize: 13, fontWeight: 600,
                cursor: 'pointer',
              }}
            >
              Mode: {config.mode} — Switch to {config.mode === 'AUTO' ? 'SUGGEST' : 'AUTO'}
            </button>
          )}
          <button
            onClick={handleReload}
            disabled={reloading}
            style={{
              background: reloading ? 'var(--ink-3)' : 'var(--accent)',
              color: 'var(--surface)', border: 'none', borderRadius: 6,
              padding: '9px 18px', fontSize: 13, fontWeight: 600,
              cursor: reloading ? 'not-allowed' : 'pointer',
            }}
          >
            {reloading ? 'Reloading…' : 'Reload from file'}
          </button>
        </div>
      </div>

      {reloadMsg && (
        <div style={{
          marginBottom: 16, padding: '10px 16px', borderRadius: 6, fontSize: 13,
          background: reloadMsg.startsWith('Reload failed') ? 'var(--crit-soft)' : 'var(--ok-soft)',
          border: `1px solid ${reloadMsg.startsWith('Reload failed') ? 'color-mix(in srgb, var(--crit) 45%, transparent)' : 'color-mix(in srgb, var(--ok) 45%, transparent)'}`,
          color: reloadMsg.startsWith('Reload failed') ? 'var(--crit)' : 'var(--ok)',
        }}>
          {reloadMsg}
        </div>
      )}

      {error && (
        <div style={{
          padding: '12px 16px', borderRadius: 8, marginBottom: 16,
          background: 'var(--crit-soft)', border: '1px solid color-mix(in srgb, var(--crit) 45%, transparent)', color: 'var(--crit)', fontSize: 14,
        }}>
          {error}
        </div>
      )}

      {loading ? (
        <p style={{ color: 'var(--ink-3)' }}>Loading…</p>
      ) : config ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

          {/* Mode + auto-route-disabled */}
          <div style={{
            background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 10, padding: 20,
          }}>
            <h3 style={{ margin: '0 0 12px', fontSize: 15, color: 'var(--ink)' }}>Global Settings</h3>
            <div style={{ display: 'flex', gap: 32, flexWrap: 'wrap' }}>
              <div>
                <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink-2)', marginBottom: 4 }}>Mode</div>
                <span style={{
                  background: config.mode === 'AUTO' ? 'var(--ok-soft)' : 'var(--surface-3)',
                  color: config.mode === 'AUTO' ? 'var(--ok)' : 'var(--ink-2)',
                  border: `1px solid ${config.mode === 'AUTO' ? 'color-mix(in srgb, var(--ok) 45%, transparent)' : 'var(--line)'}`,
                  borderRadius: 20, padding: '3px 12px', fontSize: 13, fontWeight: 600,
                }}>
                  {config.mode}
                </span>
              </div>
              <div>
                <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink-2)', marginBottom: 4 }}>Manual Review Folder</div>
                <code style={{ fontSize: 13, color: 'var(--ink)' }}>{config.manualReviewFolder}</code>
              </div>
              <div>
                <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink-2)', marginBottom: 4 }}>
                  Auto-route above
                </div>
                <span style={{
                  fontSize: 15, fontWeight: 700, color: 'var(--ink)',
                  fontVariantNumeric: 'tabular-nums',
                }}>
                  {config.autoRouteMinConfidence != null
                    ? `${Math.round(config.autoRouteMinConfidence * 100)}%`
                    : '—'}
                </span>
                <div style={{ fontSize: 11, color: 'var(--ink-3)', marginTop: 3, maxWidth: 260 }}>
                  Anything less confident is filed to {config.manualReviewFolder} for a person.
                </div>
              </div>
              <div>
                <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink-2)', marginBottom: 4 }}>Always Manual Review</div>
                {config.autoRouteDisabled.length > 0
                  ? config.autoRouteDisabled.map(c => (
                    <span key={c} style={{
                      background: 'var(--warn-soft)', border: '1px solid color-mix(in srgb, var(--warn) 45%, transparent)',
                      color: 'var(--warn)', borderRadius: 4, padding: '2px 8px',
                      fontSize: 12, marginRight: 4,
                    }}>{c}</span>
                  ))
                  : <span style={{ fontSize: 12, color: 'var(--ink-3)' }}>none</span>
                }
              </div>
            </div>
          </div>

          {/* Category rules */}
          <div style={{
            background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 10, padding: 20,
          }}>
            <h3 style={{ margin: '0 0 12px', fontSize: 15, color: 'var(--ink)' }}>Category Rules</h3>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ background: 'var(--surface-2)' }}>
                  <th style={{ padding: '8px 12px', textAlign: 'left', color: 'var(--ink-2)',
                    borderBottom: '2px solid var(--line)', fontWeight: 600 }}>Category</th>
                  <th style={{ padding: '8px 12px', textAlign: 'left', color: 'var(--ink-2)',
                    borderBottom: '2px solid var(--line)', fontWeight: 600 }}>Folder</th>
                  <th style={{ padding: '8px 12px', textAlign: 'left', color: 'var(--ink-2)',
                    borderBottom: '2px solid var(--line)', fontWeight: 600 }}>Note</th>
                </tr>
              </thead>
              <tbody>
                {config.rules.map((rule, i) => {
                  const isDisabled = config.autoRouteDisabled.includes(rule.category);
                  return (
                    <tr key={rule.category} style={{
                      background: i % 2 === 0 ? 'var(--surface)' : 'var(--surface-2)',
                      borderBottom: '1px solid var(--line)',
                    }}>
                      <td style={{ padding: '8px 12px', fontFamily: 'monospace', color: 'var(--ink)' }}>
                        {rule.category}
                      </td>
                      <td style={{ padding: '8px 12px', fontFamily: 'monospace', color: 'var(--accent)' }}>
                        {rule.folder}
                      </td>
                      <td style={{ padding: '8px 12px' }}>
                        {isDisabled && (
                          <span style={{
                            background: 'var(--warn-soft)', border: '1px solid color-mix(in srgb, var(--warn) 45%, transparent)',
                            color: 'var(--warn)', borderRadius: 4, padding: '2px 8px', fontSize: 11,
                          }}>always manual review</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Subtype overrides */}
          {config.subtypeOverrides.length > 0 && (
            <div style={{
              background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 10, padding: 20,
            }}>
              <h3 style={{ margin: '0 0 12px', fontSize: 15, color: 'var(--ink)' }}>Subtype Overrides</h3>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ background: 'var(--surface-2)' }}>
                    {['Category', 'Subtype', 'Folder'].map(h => (
                      <th key={h} style={{ padding: '8px 12px', textAlign: 'left',
                        color: 'var(--ink-2)', borderBottom: '2px solid var(--line)', fontWeight: 600 }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {config.subtypeOverrides.map((so, i) => (
                    <tr key={i} style={{
                      background: i % 2 === 0 ? 'var(--surface)' : 'var(--surface-2)',
                      borderBottom: '1px solid var(--line)',
                    }}>
                      <td style={{ padding: '8px 12px', fontFamily: 'monospace' }}>{so.category}</td>
                      <td style={{ padding: '8px 12px', fontFamily: 'monospace' }}>{so.subtype}</td>
                      <td style={{ padding: '8px 12px', fontFamily: 'monospace', color: 'var(--accent)' }}>{so.folder}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Sender overrides */}
          <div style={{
            background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 10, padding: 20,
          }}>
            <h3 style={{ margin: '0 0 12px', fontSize: 15, color: 'var(--ink)' }}>Sender Overrides</h3>
            {config.senderOverrides.length === 0 ? (
              <p style={{ fontSize: 13, color: 'var(--ink-3)', margin: 0 }}>
                None configured. Add entries to <code>routing-config.yml</code> to route specific senders to fixed folders.
              </p>
            ) : (
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ background: 'var(--surface-2)' }}>
                    {['Sender Fax (E.164)', 'Folder'].map(h => (
                      <th key={h} style={{ padding: '8px 12px', textAlign: 'left',
                        color: 'var(--ink-2)', borderBottom: '2px solid var(--line)', fontWeight: 600 }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {config.senderOverrides.map((so, i) => (
                    <tr key={i} style={{ borderBottom: '1px solid var(--line)' }}>
                      <td style={{ padding: '8px 12px', fontFamily: 'monospace' }}>{so.senderFax}</td>
                      <td style={{ padding: '8px 12px', fontFamily: 'monospace', color: 'var(--accent)' }}>{so.folder}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}
