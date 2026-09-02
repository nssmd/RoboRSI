import { useLayoutEffect, useRef, useState } from 'react';
import type { SessionTurns, Turn } from '../lib/types';
import { clockOf, roleHue } from '../lib/format';
import { EmptyHint, PanelHeader } from './primitives';
import { renderMessage } from './renderMessage';

/**
 * The multi-turn conversation for the selected session — an EventStream-style
 * transcript: user/assistant alternating, timestamped, role-tinted, with JSON
 * and code blocks in assistant text rendered as pretty-printed blocks.
 * Auto-follows the tail unless the reader has scrolled up.
 */
export function EventStream({
  turns,
  loading,
  connected,
  sessionRole,
}: {
  turns: SessionTurns | undefined;
  loading: boolean;
  connected: boolean;
  sessionRole: string;
}) {
  const [following, setFollowing] = useState(true);
  const scroller = useRef<HTMLDivElement>(null);
  const rows = turns?.turns ?? [];

  useLayoutEffect(() => {
    if (following && scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight;
  }, [rows.length, following]);

  return (
    <section className="card flex min-h-0 flex-1 flex-col">
      <PanelHeader
        title="Conversation"
        right={
          <div className="flex items-center gap-3">
            <span className="text-[10px] text-ink-faint">{rows.length} turns</span>
            <span className={`text-[10px] ${connected ? 'text-ok' : 'text-ink-faint'}`}>
              {connected ? '● live' : '○ reconnecting'}
            </span>
          </div>
        }
      />
      <div
        ref={scroller}
        onScroll={(e) => {
          const el = e.currentTarget;
          setFollowing(el.scrollHeight - el.scrollTop - el.clientHeight < 60);
        }}
        className="min-h-0 flex-1 overflow-y-auto scroll-thin"
      >
        {loading && rows.length === 0 ? (
          <EmptyHint>loading transcript…</EmptyHint>
        ) : rows.length === 0 ? (
          <EmptyHint>no transcript for this session yet</EmptyHint>
        ) : (
          rows.map((turn, i) => <TurnRow key={i} turn={turn} sessionRole={sessionRole} />)
        )}
      </div>
    </section>
  );
}

function TurnRow({ turn, sessionRole }: { turn: Turn; sessionRole: string }) {
  const isUser = turn.role === 'user';
  // Assistant rows carry the session's role hue; user (task) rows stay neutral.
  const hue = isUser ? '#7e7d75' : roleHue(sessionRole);
  const label = isUser ? 'task' : sessionRole || 'agent';
  return (
    <div className="grid grid-cols-[86px_minmax(0,1fr)] gap-2 border-b border-line/30 px-3 py-2.5 last:border-b-0">
      <div className="pt-0.5">
        <div
          className="text-[10px] font-semibold uppercase tracking-wide"
          style={{ color: hue }}
        >
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
