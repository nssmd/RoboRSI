import { useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { useEvolution, useTaskEvolution } from '../lib/hooks';
import type { EvolutionTask, FunnelTally, Hypothesis, Lead } from '../lib/types';
import { bucketHue, compact } from '../lib/format';
import { EmptyHint, FunnelBar, PanelHeader, Stat } from './primitives';

/**
 * "Evolution" view — how the skill library self-improves. Left: the global
 * overview (KPIs, per-queue review funnel, and a task ranking by verified
 * successes / approved leads). Right: the detail for the selected task — its
 * full Manager-approved leads, the failure-hypothesis funnel, and the run
 * success trend. All data comes from /api/evolution + /api/tasks/{t}/evolution
 * (same disk sources as scripts/evo_dashboard.py).
 */
export function EvolutionView() {
  const q = useEvolution();
  const data = q.data;
  const [selected, setSelected] = useState<string | null>(null);

  const totals = data?.totals ?? {};
  const kpis = useMemo(() => globalKpis(data?.tasks ?? [], totals), [data, totals]);
  const activeTask = selected ?? data?.tasks[0]?.task ?? null;

  return (
    <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 p-3 xl:grid-cols-[minmax(0,1fr)_460px]">
      <div className="flex min-h-0 flex-col gap-3">
        <section className="card shrink-0">
          <PanelHeader
            title="Skill self-evolution"
            right={<span className="text-[10px] text-ink-faint">{data?.task_count ?? 0} active tasks</span>}
          />
          <div className="grid grid-cols-2 gap-2.5 p-3 sm:grid-cols-4">
            <Stat label="tasks w/ leads" value={kpis.tasksWithLeads} accent="#c7a66a" />
            <Stat label="approved leads" value={kpis.leads} accent="#7fa386" hint="Manager-approved wiki leads across all tasks" />
            <Stat label="verified wins" value={kpis.verified} accent="#7fa386" />
            <Stat label="pending review" value={compact(kpis.pending)} accent="#c1a363" hint="proposals awaiting the Manager gate" />
          </div>
          <div className="grid grid-cols-1 gap-2.5 border-t border-line-soft px-3 py-3 sm:grid-cols-3">
            {Object.entries(totals).map(([queue, tally]) => (
              <QueueFunnel key={queue} queue={queue} tally={tally} />
            ))}
          </div>
        </section>

        <TaskRanking
          tasks={data?.tasks ?? []}
          loading={q.isLoading}
          activeTask={activeTask}
          onSelect={setSelected}
        />
      </div>

      <TaskEvolutionDetail task={activeTask} />
    </div>
  );
}

function globalKpis(tasks: EvolutionTask[], totals: Record<string, FunnelTally>) {
  const leads = tasks.reduce((s, t) => s + t.leads, 0);
  const verified = tasks.reduce((s, t) => s + t.verified_success, 0);
  const tasksWithLeads = tasks.filter((t) => t.leads > 0).length;
  const pending = Object.values(totals).reduce((s, t) => s + t.pending, 0);
  return { leads, verified, tasksWithLeads, pending };
}

const QUEUE_LABEL: Record<string, string> = {
  wiki_review: 'wiki · hypotheses',
  skill_review: 'skill diffs',
  plan_review: 'plan promotions',
};

function QueueFunnel({ queue, tally }: { queue: string; tally: FunnelTally }) {
  return (
    <div className="rounded-lg border border-line-soft bg-panel-2/30 px-3 py-2.5">
      <div className="mb-1.5 flex items-baseline justify-between">
        <span className="text-[11px] font-medium text-ink-dim">{QUEUE_LABEL[queue] ?? queue}</span>
        <span className="font-mono text-[10px] tabular-nums text-ink-faint">{tally.total}</span>
      </div>
      <FunnelBar pending={tally.pending} approved={tally.approved} rejected={tally.rejected} other={tally.other} />
      <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[10px]">
        <FunnelKey bucket="pending" n={tally.pending} />
        <FunnelKey bucket="approved" n={tally.approved} />
        <FunnelKey bucket="rejected" n={tally.rejected} />
      </div>
    </div>
  );
}

function FunnelKey({ bucket, n }: { bucket: string; n: number }) {
  return (
    <span className="inline-flex items-center gap-1 tabular-nums text-ink-faint">
      <span className="inline-block h-2 w-2 rounded-sm" style={{ background: bucketHue(bucket) }} />
      {bucket} {n}
    </span>
  );
}

/* --------------------------------------------------------- task ranking */

function TaskRanking({
  tasks,
  loading,
  activeTask,
  onSelect,
}: {
  tasks: EvolutionTask[];
  loading: boolean;
  activeTask: string | null;
  onSelect: (task: string) => void;
}) {
  const maxLeads = Math.max(1, ...tasks.map((t) => t.leads));
  return (
    <section className="card flex min-h-0 flex-1 flex-col">
      <PanelHeader
        title="Task knowledge · ranked"
        right={<span className="text-[10px] text-ink-faint">leads · wins · funnel</span>}
      />
      <div className="min-h-0 flex-1 overflow-y-auto scroll-thin">
        {loading && tasks.length === 0 ? (
          <EmptyHint>loading evolution data…</EmptyHint>
        ) : tasks.length === 0 ? (
          <EmptyHint>no accumulated task knowledge yet</EmptyHint>
        ) : (
          tasks.map((t) => (
            <TaskRankRow key={t.task} task={t} maxLeads={maxLeads} active={t.task === activeTask} onSelect={onSelect} />
          ))
        )}
      </div>
    </section>
  );
}

function TaskRankRow({
  task,
  maxLeads,
  active,
  onSelect,
}: {
  task: EvolutionTask;
  maxLeads: number;
  active: boolean;
  onSelect: (task: string) => void;
}) {
  return (
    <button
      onClick={() => onSelect(task.task)}
      aria-current={active ? 'true' : undefined}
      className={`row-hover grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-b border-line/40 border-l-2 px-3 py-2 text-left last:border-b-0 ${
        active ? 'border-l-gold bg-panel-2/50' : 'border-l-transparent'
      }`}
    >
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="truncate text-[12px] font-medium text-ink" title={task.task}>
            {task.task}
          </span>
          {task.verified_success > 0 && (
            <span className="shrink-0 rounded bg-ok/15 px-1.5 py-px text-[9px] font-semibold uppercase tracking-wide text-ok">
              solved
            </span>
          )}
        </div>
        <div className="mt-1.5 h-1 w-full overflow-hidden rounded bg-bg">
          <div className="h-full rounded bg-gold/70" style={{ width: `${(task.leads / maxLeads) * 100}%` }} />
        </div>
        <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] tabular-nums text-ink-faint">
          <span title="Manager-approved leads">
            <span className="text-gold-soft">{task.leads}</span> leads
          </span>
          <span title="verified successes">
            <span className="text-ok">{task.verified_success}</span> wins
          </span>
          <span title="wiki success / fail traces">
            {task.success_traces}✓ / {task.fail_traces}✗
          </span>
        </div>
      </div>
      <div className="w-24 shrink-0">
        <FunnelBar pending={task.hyp_pending} approved={task.hyp_approved} rejected={task.hyp_rejected} />
        <div className="mt-1 text-right font-mono text-[9px] tabular-nums text-ink-faint">
          {task.hyp_pending + task.hyp_approved + task.hyp_rejected} hyp
        </div>
      </div>
    </button>
  );
}

/* --------------------------------------------------------- task detail */

function TaskEvolutionDetail({ task }: { task: string | null }) {
  const q = useTaskEvolution(task);
  const data = q.data;
  return (
    <section className="card flex min-h-0 flex-col">
      <PanelHeader
        title={task ? `Evolution · ${task}` : 'Task evolution'}
        right={
          data ? (
            <span className="text-[10px] tabular-nums text-ink-faint">
              {data.leads.length} leads · {data.hypotheses.length} hyp
            </span>
          ) : null
        }
      />
      <div className="min-h-0 flex-1 overflow-y-auto scroll-thin p-3">
        {!task ? (
          <EmptyHint>select a task to inspect its evolution</EmptyHint>
        ) : q.isLoading && !data ? (
          <EmptyHint>loading task evolution…</EmptyHint>
        ) : !data ? (
          <EmptyHint>no evolution data</EmptyHint>
        ) : (
          <DetailBody data={data} />
        )}
      </div>
    </section>
  );
}

function DetailBody({ data }: { data: NonNullable<ReturnType<typeof useTaskEvolution>['data']> }) {
  const f = data.hyp_funnel;
  const wins = data.trend.filter((p) => p.verified).length;
  return (
    <div className="animate-fade-in space-y-4">
      <div className="grid grid-cols-4 gap-2">
        <MiniStat label="wins" value={wins} accent="#7fa386" />
        <MiniStat label="✓ traces" value={data.success_traces} />
        <MiniStat label="✗ traces" value={data.fail_traces} accent="#c77b72" />
        <MiniStat label="runs" value={data.trend.length} />
      </div>

      <TrendStrip points={data.trend} wins={wins} />

      <Section title={`Manager-approved leads · ${data.leads.length}`}>
        {data.leads.length === 0 ? (
          <p className="text-[11px] text-ink-faint">no approved leads yet</p>
        ) : (
          <div className="space-y-2">
            {data.leads.map((lead, i) => (
              <LeadCard key={i} lead={lead} index={i + 1} />
            ))}
          </div>
        )}
      </Section>

      <Section title="Failure-hypothesis funnel">
        <div className="mb-2">
          <FunnelBar pending={f.pending} approved={f.approved} rejected={f.rejected} />
          <div className="mt-1.5 flex gap-3 text-[10px] tabular-nums text-ink-faint">
            <FunnelSpan bucket="pending" n={f.pending} />
            <FunnelSpan bucket="approved" n={f.approved} />
            <FunnelSpan bucket="rejected" n={f.rejected} />
          </div>
        </div>
        <div className="space-y-1.5">
          {data.hypotheses.slice(0, 24).map((h) => (
            <HypRow key={h.id ?? Math.random()} hyp={h} />
          ))}
        </div>
      </Section>

      {data.measurements.trim() && (
        <Section title="Key measurements (human-approved)">
          <WikiBlock text={data.measurements} />
        </Section>
      )}

      {data.successful_traces.trim() && (
        <Section title="Successful execution traces">
          <WikiBlock text={data.successful_traces} />
        </Section>
      )}
    </div>
  );
}

function WikiBlock({ text }: { text: string }) {
  return (
    <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded border border-line-soft bg-bg/50 p-2.5 font-mono text-[11px] leading-relaxed text-blue-sky scroll-thin">
      {text}
    </pre>
  );
}

function MiniStat({ label, value, accent }: { label: string; value: number; accent?: string }) {
  return (
    <div className="rounded-md border border-line-soft bg-panel-2/30 px-2 py-1.5 text-center">
      <div className="font-mono text-base font-semibold leading-none tabular-nums" style={accent ? { color: accent } : undefined}>
        {value}
      </div>
      <div className="mt-0.5 text-[9px] uppercase tracking-wide text-ink-faint">{label}</div>
    </div>
  );
}

function TrendStrip({ points, wins }: { points: { verified: boolean; status: string }[]; wins: number }) {
  const shown = points.slice(-60);
  return (
    <div className="rounded-md border border-line-soft bg-bg/40 px-3 py-2.5">
      <div className="mb-2 flex items-baseline justify-between">
        <span className="text-[10px] font-medium uppercase tracking-wide text-ink-faint">run trend</span>
        <span className="font-mono text-[10px] tabular-nums text-ink-faint">
          <span className="text-ok">{wins}</span> / {points.length}
        </span>
      </div>
      {shown.length === 0 ? (
        <p className="text-[11px] text-ink-faint">no runs recorded</p>
      ) : (
        <div className="flex h-9 items-end gap-[2px]">
          {shown.map((p, i) => (
            <span
              key={i}
              className="min-w-[2px] flex-1 rounded-sm transition-[height]"
              style={{
                height: p.verified ? '100%' : p.status === 'error' ? '35%' : '22%',
                background: p.verified ? '#7fa386' : p.status === 'error' ? '#c77b72' : '#3d3e38',
              }}
              title={p.status}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function LeadCard({ lead, index }: { lead: Lead; index: number }) {
  return (
    <div className="rounded-md border border-line-soft bg-panel-2/25 p-2.5">
      <div className="mb-1 flex items-center gap-2">
        <span className="grid h-4 w-4 place-items-center rounded bg-gold/20 font-mono text-[9px] font-bold text-gold-soft">
          {index}
        </span>
        {lead.run_id && (
          <span className="truncate font-mono text-[10px] text-ink-faint" title={lead.run_id}>
            {lead.run_id}
          </span>
        )}
      </div>
      <p className="whitespace-pre-wrap break-words text-[11.5px] leading-relaxed text-ink-dim">{lead.text}</p>
      {lead.root_cause && (
        <p className="mt-1.5 border-l-2 border-reviewer/50 pl-2 text-[11px] leading-relaxed text-ink-faint">
          <span className="font-medium text-reviewer">root cause · </span>
          {lead.root_cause}
        </p>
      )}
      {lead.approved && (
        <p className="mt-1.5 border-l-2 border-manager/50 pl-2 text-[11px] leading-relaxed text-ink-faint">
          <span className="font-medium text-manager">approved · </span>
          {lead.approved}
        </p>
      )}
    </div>
  );
}

function HypRow({ hyp }: { hyp: Hypothesis }) {
  const hue = bucketHue(hyp.bucket);
  return (
    <div className="row-hover flex gap-2 rounded border border-line-soft/60 bg-bg/20 px-2 py-1.5">
      <span className="mt-1 inline-block h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: hue }} title={hyp.status ?? ''} />
      <div className="min-w-0 flex-1">
        <p className="break-words text-[11px] leading-snug text-ink-dim">{hyp.root_cause || '(no root cause)'}</p>
        {hyp.next_action && (
          <p className="mt-0.5 break-words text-[10.5px] leading-snug text-ink-faint">
            <span className="text-blue">next · </span>
            {hyp.next_action}
          </p>
        )}
        {hyp.manager_note && (
          <p className="mt-0.5 break-words text-[10.5px] leading-snug text-ink-faint">
            <span style={{ color: hue }}>{hyp.bucket} · </span>
            {hyp.manager_note}
          </p>
        )}
      </div>
    </div>
  );
}

function FunnelSpan({ bucket, n }: { bucket: string; n: number }) {
  return (
    <span className="inline-flex items-center gap-1">
      <span className="inline-block h-2 w-2 rounded-sm" style={{ background: bucketHue(bucket) }} />
      {bucket} {n}
    </span>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-faint">{title}</h3>
      {children}
    </div>
  );
}
