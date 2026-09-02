import { useState } from 'react';
import { useManager } from '../lib/hooks';
import type { Lane, ManagerOverview, ManagerSession, ManagerTotals } from '../lib/types';
import { ago, compact } from '../lib/format';
import { EmptyHint, PanelHeader, Stat } from './primitives';
import { LaneCard } from './manager/LaneCard';
import { TaskGroupCard } from './manager/TaskGroupCard';
import { ManagerActivity } from './manager/ManagerActivity';

/**
 * The Manager-centric landing page. Lists the REAL manager sessions
 * (backend-agnostic — e.g. a RoboTwin manager + a libero manager, each a
 * top-level claude/codex/copilot session), selectable via a switcher. The
 * selected manager's activity + what it drives (campaign lanes, per-task
 * planner/reviewer sessions) are laid out below. The campaign lanes/tasks here
 * belong to the RoboTwin manager. All data comes from /api/manager.
 */
export function ManagerView({
  onOpenSession,
  onOpenEvolution,
}: {
  onOpenSession: (key: string) => void;
  onOpenEvolution: () => void;
}) {
  const q = useManager();
  const data = q.data;
  const managers = data?.managers ?? [];
  const [selId, setSelId] = useState<string | null>(null);
  const selected = managers.find((m) => m.id === selId) ?? managers[0];

  return (
    <div className="min-h-0 flex-1 overflow-y-auto scroll-thin">
      <div className="mx-auto max-w-[1400px] space-y-3 p-3">
        <ManagerHeader
          managers={managers}
          selected={selected}
          onSelect={setSelId}
          totals={data?.totals}
          lanes={data?.lanes ?? []}
          loading={q.isLoading}
          onOpenEvolution={onOpenEvolution}
        />
        <LanesSection lanes={data?.lanes ?? []} loading={q.isLoading} />
        <TasksSection data={data} loading={q.isLoading} onOpenSession={onOpenSession} />
        <ManagerActivity manager={selected} />
      </div>
    </div>
  );
}

/* ------------------------------------------------------------ header card */

function ManagerHeader({
  managers,
  selected,
  onSelect,
  totals,
  lanes,
  loading,
  onOpenEvolution,
}: {
  managers: ManagerSession[];
  selected: ManagerSession | undefined;
  onSelect: (id: string) => void;
  totals: ManagerTotals | undefined;
  lanes: Lane[];
  loading: boolean;
  onOpenEvolution: () => void;
}) {
  const liveLanes = lanes.filter((l) => l.current).length;
  return (
    <section className="card overflow-hidden">
      <div className="flex flex-wrap items-center gap-3 border-b border-line bg-gradient-to-b from-surface to-panel/40 px-4 py-3">
        <span
          aria-hidden="true"
          className="h-9 w-1 shrink-0 rounded-full bg-manager"
          style={{ boxShadow: '0 0 14px -2px #c7a66a' }}
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h1 className="text-sm font-semibold text-ink">Managers</h1>
            <span className="chip text-manager" style={{ borderColor: '#c7a66a44' }}>
              {managers.length} live
            </span>
          </div>
          <p className="mt-0.5 truncate text-[11px] text-ink-faint">
            Top-level agent sessions (backend-agnostic) that supervise the campaigns, gate the
            review funnel, and steer per-task planner/reviewer sessions.
          </p>
        </div>
      </div>
      <div className="flex flex-wrap gap-2 border-b border-line/60 px-3 py-2">
        {managers.length === 0 ? (
          <span className="text-[11px] text-ink-faint">
            {loading ? 'loading managers…' : 'no manager sessions registered'}
          </span>
        ) : (
          managers.map((m) => {
            const on = selected?.id === m.id;
            return (
              <button
                key={m.id}
                onClick={() => onSelect(m.id)}
                className={`rounded border px-2.5 py-1.5 text-left transition-colors ${
                  on ? 'border-manager/60 bg-manager/10' : 'border-line hover:bg-panel/60'
                }`}
              >
                <div className="flex items-center gap-1.5 text-[11px]">
                  <span className="font-semibold capitalize text-ink">{m.topic}</span>
                  <span className="chip text-ink-faint">{m.backend}</span>
                  <span className="font-mono text-[10px] text-ink-faint">{m.id.slice(0, 8)}</span>
                </div>
                <div className="mt-0.5 text-[10px] text-ink-faint">
                  active {ago(m.last_active)} · {m.turn_count} turns
                </div>
              </button>
            );
          })
        )}
      </div>
      <div className="grid grid-cols-2 gap-2.5 p-3 sm:grid-cols-4">
        <Stat label="verified wins" value={loading ? '·' : totals?.verified_success ?? 0} accent="#7fa386" hint="predicate-verified successes across all tasks (trace.db)" />
        <button className="text-left" onClick={onOpenEvolution} title="open skill self-evolution">
          <Stat label="pending review" value={loading ? '·' : compact(totals?.pending_review ?? 0)} accent="#c1a363" hint="proposals awaiting the Manager gate (wiki / skill / plan)" />
        </button>
        <Stat label="tasks steered" value={loading ? '·' : totals?.orchestrated_tasks ?? 0} accent="#c7a66a" hint="tasks with persistent planner/reviewer sessions" />
        <Stat label="lanes live" value={loading ? '·' : `${liveLanes}/${lanes.length}`} accent="#8fa7b8" hint="campaign daemons currently running a rollout" />
      </div>
    </section>
  );
}

/* ----------------------------------------------------------- lanes section */

function LanesSection({ lanes, loading }: { lanes: Lane[]; loading: boolean }) {
  return (
    <section className="card">
      <PanelHeader
        title="Campaign daemons"
        right={<span className="text-[10px] text-ink-faint">RoboTwin · 2 lanes</span>}
      />
      {loading && lanes.length === 0 ? (
        <EmptyHint>loading campaign lanes…</EmptyHint>
      ) : lanes.length === 0 ? (
        <EmptyHint>no campaign lanes running</EmptyHint>
      ) : (
        <div className="grid grid-cols-1 gap-3 p-3 lg:grid-cols-2">
          {lanes.map((lane) => (
            <LaneCard key={lane.id} lane={lane} />
          ))}
        </div>
      )}
    </section>
  );
}

/* ----------------------------------------------------------- tasks section */

function TasksSection({
  data,
  loading,
  onOpenSession,
}: {
  data: ManagerOverview | undefined;
  loading: boolean;
  onOpenSession: (key: string) => void;
}) {
  const groups = data?.task_groups ?? [];
  return (
    <section className="card">
      <PanelHeader
        title="Tasks & role sessions"
        right={<span className="text-[10px] text-ink-faint">{groups.length} tasks · planner + reviewer</span>}
      />
      {loading && groups.length === 0 ? (
        <EmptyHint>loading orchestrated tasks…</EmptyHint>
      ) : groups.length === 0 ? (
        <EmptyHint>no per-task role sessions yet</EmptyHint>
      ) : (
        <div className="p-3">
          <div className="grid grid-cols-1 gap-2.5 lg:grid-cols-2">
            {groups.map((g) => (
              <TaskGroupCard key={g.task} group={g} onOpenSession={onOpenSession} />
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
