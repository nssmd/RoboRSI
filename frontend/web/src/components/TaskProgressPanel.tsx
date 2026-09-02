import { useTaskProgress } from '../lib/hooks';
import { clockOf, statusColor } from '../lib/format';
import { EmptyHint, PanelHeader } from './primitives';

/** Runs table + verified-success tally for one task (read from trace.db). */
export function TaskProgressPanel({ task }: { task: string | null }) {
  const q = useTaskProgress(task);
  const data = q.data;
  return (
    <section className="card flex min-h-0 flex-col">
      <PanelHeader
        title={task ? `Runs · ${task}` : 'Runs'}
        right={
          data ? (
            <span className="text-[10px] text-ink-faint">
              <span className="text-ok">{data.verified_success}</span> verified / {data.total_runs} runs
            </span>
          ) : null
        }
      />
      <div className="min-h-0 flex-1 overflow-y-auto scroll-thin">
        {!task ? (
          <EmptyHint>select a session to see its task runs</EmptyHint>
        ) : q.isLoading && !data ? (
          <EmptyHint>loading runs…</EmptyHint>
        ) : !data || data.recent.length === 0 ? (
          <EmptyHint>no runs recorded for this task</EmptyHint>
        ) : (
          data.recent.map((r) => (
            <div
              key={r.id}
              className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 border-b border-line/40 px-3 py-1.5 text-[11px] last:border-b-0"
            >
              <div className="min-w-0">
                <span className="font-mono text-ink-dim" style={{ color: statusColor(r.status, r.verified) }}>
                  {r.verified ? '✔ success' : r.status}
                </span>
                <span className="ml-2 text-ink-faint">
                  seed {r.seed ?? '—'}
                  {r.outcome ? ` · ${r.outcome}` : ''}
                </span>
              </div>
              <span className="font-mono text-[10px] tabular-nums text-ink-faint">
                {clockOf(r.started_at ? r.started_at.replace(' ', 'T') : null)}
              </span>
            </div>
          ))
        )}
      </div>
    </section>
  );
}
