import type { SessionRow, SessionTurns } from '../lib/types';
import { roleHue } from '../lib/format';
import { Chip, StatusDot } from './primitives';

/**
 * Top bar for the selected session: task, role, verified-success state, the
 * turn count, and the Claude thread_id. `streamOk` reflects the live campaign
 * WS connection.
 */
export function TopBar({
  session,
  turns,
  streamOk,
}: {
  session: SessionRow;
  turns: SessionTurns | undefined;
  streamOk: boolean;
}) {
  const hue = roleHue(session.role);
  const count = turns?.turns.length ?? 0;
  return (
    <header className="flex min-h-[58px] items-center gap-3 border-b border-line bg-gradient-to-b from-surface to-panel/40 px-5 py-2.5">
      <span
        aria-hidden="true"
        className="h-8 w-1 shrink-0 rounded-full"
        style={{ background: hue, boxShadow: `0 0 12px -2px ${hue}` }}
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <StatusDot ok={session.task_success} title={session.task_success ? 'verified success' : 'unsolved'} />
          <h1 className="truncate text-sm font-semibold text-ink">{session.task}</h1>
          <Chip color={hue} className="capitalize">
            {session.role || 'session'}
          </Chip>
          {session.task_success ? (
            <Chip color="#7fa386">✓ solved · {session.success_count}</Chip>
          ) : (
            <Chip>unsolved</Chip>
          )}
          <span className="hidden items-center gap-1.5 text-[11px] text-ink-faint lg:inline-flex">
            {count} turn{count === 1 ? '' : 's'}
            <span className={streamOk ? 'text-ok' : 'text-ink-faint'}>{streamOk ? '● live' : '○ reconnecting'}</span>
          </span>
        </div>
        <p className="mt-0.5 truncate font-mono text-[11px] text-ink-faint" title={session.thread_id}>
          thread {session.thread_id}
        </p>
      </div>
    </header>
  );
}
