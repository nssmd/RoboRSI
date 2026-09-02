import { useState } from 'react';
import type { ManagerTaskGroup, RoleSession } from '../../lib/types';
import { useTaskProgress } from '../../lib/hooks';
import { ago, clockOf, roleHue, statusColor } from '../../lib/format';

/**
 * One orchestrated task: a header with verified-success / approved-lead badges,
 * expandable to reveal its planner + reviewer role sessions (each opens the
 * multi-turn transcript) and a compact recent-runs slice (lazily fetched from
 * /api/tasks/{task}/progress only while expanded).
 */
export function TaskGroupCard({
  group,
  onOpenSession,
}: {
  group: ManagerTaskGroup;
  onOpenSession: (key: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const solved = group.verified_success > 0;
  return (
    <div className={`rounded-lg border bg-panel-2/25 ${open ? 'border-line' : 'border-line-soft'}`}>
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="row-hover flex w-full items-center gap-2 px-3 py-2.5 text-left"
      >
        <span
          aria-hidden="true"
          className="inline-block h-1.5 w-1.5 shrink-0 rounded-full"
          style={{ background: solved ? '#7fa386' : '#3d3e38' }}
        />
        <span className="min-w-0 flex-1 truncate text-[12.5px] font-medium text-ink" title={group.task}>
          {group.task}
        </span>
        <TaskBadges group={group} solved={solved} />
        <span className={`shrink-0 text-[10px] text-ink-faint transition-transform ${open ? 'rotate-90' : ''}`}>▸</span>
      </button>
      {open && (
        <div className="border-t border-line-soft px-3 py-2.5">
          <RoleList sessions={group.sessions} onOpenSession={onOpenSession} />
          <RecentRuns task={group.task} />
        </div>
      )}
    </div>
  );
}

function TaskBadges({ group, solved }: { group: ManagerTaskGroup; solved: boolean }) {
  return (
    <span className="flex shrink-0 items-center gap-2 text-[10px] tabular-nums text-ink-faint">
      {solved && (
        <span className="rounded bg-ok/15 px-1.5 py-px text-[9px] font-semibold uppercase tracking-wide text-ok">
          {group.verified_success} win{group.verified_success === 1 ? '' : 's'}
        </span>
      )}
      {group.leads > 0 && (
        <span title="Manager-approved wiki leads">
          <span className="text-gold-soft">{group.leads}</span> leads
        </span>
      )}
      <span>{group.sessions.length} roles</span>
    </span>
  );
}

/* --------------------------------------------------------------- role list */

function RoleList({
  sessions,
  onOpenSession,
}: {
  sessions: RoleSession[];
  onOpenSession: (key: string) => void;
}) {
  return (
    <div className="space-y-1">
      {sessions.map((s) => (
        <RoleRow key={s.key} session={s} onOpenSession={onOpenSession} />
      ))}
    </div>
  );
}

function RoleRow({
  session,
  onOpenSession,
}: {
  session: RoleSession;
  onOpenSession: (key: string) => void;
}) {
  const hue = roleHue(session.role);
  return (
    <button
      onClick={() => onOpenSession(session.key)}
      title={session.thread_id}
      className="row-hover flex w-full items-center gap-2 rounded border border-line-soft/60 bg-bg/20 px-2.5 py-1.5 text-left"
    >
      <span className="inline-block h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: hue }} />
      <span className="w-16 shrink-0 text-[11px] font-medium capitalize" style={{ color: hue }}>
        {session.role || 'session'}
      </span>
      <span className="min-w-0 flex-1 truncate font-mono text-[10px] text-ink-faint">
        {session.thread_id.slice(0, 8)}
      </span>
      <span className="shrink-0 text-[10px] tabular-nums text-ink-faint">
        {session.has_transcript ? ago(session.last_active) : 'no transcript'}
      </span>
      <span className="shrink-0 text-[10px] text-ink-faint">→</span>
    </button>
  );
}

/* ------------------------------------------------------------ recent runs */

function RecentRuns({ task }: { task: string }) {
  const q = useTaskProgress(task);
  const data = q.data;
  const recent = (data?.recent ?? []).slice(0, 5);
  return (
    <div className="mt-2.5 border-t border-line-soft/60 pt-2">
      <div className="mb-1 flex items-baseline justify-between">
        <span className="text-[9px] font-semibold uppercase tracking-wide text-ink-faint">recent runs</span>
        {data && (
          <span className="font-mono text-[10px] tabular-nums text-ink-faint">
            <span className="text-ok">{data.verified_success}</span> verified / {data.total_runs}
          </span>
        )}
      </div>
      {q.isLoading && !data ? (
        <div className="text-[10px] text-ink-faint">loading runs…</div>
      ) : recent.length === 0 ? (
        <div className="text-[10px] text-ink-faint">no runs recorded</div>
      ) : (
        <div className="space-y-0.5">
          {recent.map((r) => (
            <div key={r.id} className="flex items-baseline justify-between gap-2 text-[10.5px]">
              <span className="min-w-0 truncate font-mono" style={{ color: statusColor(r.status, r.verified) }}>
                {r.verified ? '✔ success' : r.status}
                <span className="ml-1.5 text-ink-faint">seed {r.seed ?? '—'}</span>
              </span>
              <span className="shrink-0 font-mono text-[9px] tabular-nums text-ink-faint">
                {clockOf(r.started_at ? r.started_at.replace(' ', 'T') : null)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
