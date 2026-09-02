import type { ManagerSession } from '../../lib/types';
import { clockOf } from '../../lib/format';
import { EmptyHint, PanelHeader } from '../primitives';
import { renderMessage } from '../renderMessage';

/**
 * The selected Manager's own recent activity — a bounded tail of its live
 * decision transcript (approve/reject calls, supervision dialogue). Manager
 * sessions are top-level (not role) sessions, so there is no per-session turns
 * endpoint; this tail is the view.
 */
export function ManagerActivity({ manager }: { manager: ManagerSession | undefined }) {
  const turns = manager?.recent_turns ?? [];
  return (
    <section className="card">
      <PanelHeader
        title="Manager activity"
        right={
          manager ? (
            <span className="text-[10px] text-ink-faint">
              {manager.turn_count} turns · latest {turns.length}
            </span>
          ) : null
        }
      />
      {turns.length === 0 ? (
        <EmptyHint>no manager activity yet</EmptyHint>
      ) : (
        <div className="max-h-[440px] overflow-y-auto scroll-thin px-3 py-2">
          {turns.map((turn, i) => (
            <TurnRow key={`${turn.ts ?? 'na'}-${i}`} turn={turn} />
          ))}
        </div>
      )}
    </section>
  );
}

function TurnRow({ turn }: { turn: { role: string; text: string; ts: string | null } }) {
  const isUser = turn.role === 'user';
  const hue = isUser ? '#7e7d75' : '#c7a66a';
  const label = isUser ? 'prompt' : 'manager';
  return (
    <div className="grid grid-cols-[76px_minmax(0,1fr)] gap-2 border-b border-line/30 py-2 last:border-b-0">
      <div className="pt-0.5">
        <div className="text-[10px] font-semibold uppercase tracking-wide" style={{ color: hue }}>
          {label}
        </div>
        <div className="mt-0.5 font-mono text-[10px] tabular-nums text-ink-faint">{clockOf(turn.ts)}</div>
      </div>
      <div className={`min-w-0 text-[12px] ${isUser ? 'text-ink-dim' : 'text-ink'}`}>
        {renderMessage(turn.text)}
      </div>
    </div>
  );
}
