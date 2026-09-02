import type { SessionRow } from '../lib/types';
import { ago, roleHue } from '../lib/format';
import { PanelHeader } from './primitives';

/**
 * Compact ledger of the roles working the currently-selected task. Colour is a
 * small state marker per role; the active session is highlighted.
 */
export function RolesPanel({
  sessions,
  activeKey,
  onSelect,
}: {
  sessions: SessionRow[];
  activeKey: string | null;
  onSelect: (key: string) => void;
}) {
  return (
    <section className="card">
      <PanelHeader title="Roles on this task" />
      <div>
        {sessions.map((s) => {
          const hue = roleHue(s.role);
          const active = s.key === activeKey;
          return (
            <button
              key={s.key}
              onClick={() => onSelect(s.key)}
              className={`grid w-full grid-cols-[100px_minmax(0,1fr)_auto] items-center gap-2 border-b border-line/60 px-3 py-2 text-left transition-colors last:border-b-0 ${
                active ? 'bg-panel' : 'hover:bg-panel/60'
              }`}
            >
              <span className="flex items-center gap-1.5">
                <span className="inline-block h-1.5 w-1.5 rounded-full" style={{ background: hue }} />
                <span className="text-[11px] font-medium capitalize" style={{ color: hue }}>
                  {s.role || 'session'}
                </span>
              </span>
              <span className="min-w-0 truncate font-mono text-[10px] text-ink-faint" title={s.thread_id}>
                {s.thread_id.slice(0, 8)}
              </span>
              <span className="text-right text-[10px] tabular-nums text-ink-faint">{ago(s.last_active)}</span>
            </button>
          );
        })}
      </div>
    </section>
  );
}
