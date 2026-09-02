import { useLayoutEffect, useRef } from 'react';
import { useCampaign, useTasks } from '../lib/hooks';
import { EmptyHint, PanelHeader } from './primitives';

/**
 * Overview view (shown when no session is selected, or "Campaign & tasks" is
 * chosen): per-task run tally (verified successes vs total) and a live-tailing
 * campaign.log. Successes/failures come from trace.db; the log from /tmp/pb.
 * The campaign WS is owned by App and passed in as `lines`/`connected` so the
 * whole app holds a single stream connection.
 */
export function CampaignView({ lines, connected }: { lines: string[]; connected: boolean }) {
  const tasksQ = useTasks();
  const campaignQ = useCampaign();
  const campaign = campaignQ.data;

  return (
    <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 p-3 xl:grid-cols-[minmax(0,1fr)_420px]">
      <CampaignLog lines={lines} connected={connected} />
      <div className="flex min-h-0 flex-col gap-3">
        <section className="card shrink-0">
          <PanelHeader title="Campaign" />
          <div className="space-y-1 px-3 py-2 text-[11px]">
            <Line label="lane A" value={campaign?.current || '—'} />
            <Line label="lane B" value={campaign?.current_b || '—'} />
            <Line label="log lines" value={String(campaign?.log_total ?? '—')} />
          </div>
        </section>
        <TaskTallyPanel tasks={tasksQ.data} />
      </div>
    </div>
  );
}

function Line({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline gap-2">
      <span className="w-16 shrink-0 text-[10px] uppercase tracking-wide text-ink-faint">{label}</span>
      <span className="min-w-0 truncate font-mono text-ink-dim" title={value}>
        {value}
      </span>
    </div>
  );
}

function TaskTallyPanel({ tasks }: { tasks: { task: string; total: number; verified_success: number }[] | undefined }) {
  return (
    <section className="card flex min-h-0 flex-col">
      <PanelHeader title="Tasks" right={<span className="text-[10px] text-ink-faint">{tasks?.length ?? 0}</span>} />
      <div className="min-h-0 flex-1 overflow-y-auto scroll-thin">
        {!tasks || tasks.length === 0 ? (
          <EmptyHint>no runs recorded</EmptyHint>
        ) : (
          tasks.map((t) => {
            const rate = t.total ? t.verified_success / t.total : 0;
            return (
              <div key={t.task} className="row-hover border-b border-line/40 px-3 py-2 last:border-b-0">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="flex min-w-0 items-center gap-1.5">
                    <span
                      className="inline-block h-1.5 w-1.5 shrink-0 rounded-full"
                      style={{ background: t.verified_success ? '#7fa386' : '#3d3e38' }}
                    />
                    <span className="min-w-0 truncate text-[11px] text-ink-dim" title={t.task}>
                      {t.task}
                    </span>
                  </span>
                  <span className="shrink-0 font-mono text-[10px] tabular-nums text-ink-faint">
                    <span className={t.verified_success ? 'text-ok' : 'text-ink-faint'}>{t.verified_success}</span>
                    /{t.total}
                  </span>
                </div>
                <div className="mt-1.5 h-1 w-full overflow-hidden rounded bg-bg">
                  <div
                    className="h-full rounded bg-ok transition-[width] duration-500"
                    style={{ width: `${Math.max(rate * 100, t.verified_success ? 3 : 0)}%` }}
                  />
                </div>
              </div>
            );
          })
        )}
      </div>
    </section>
  );
}

function CampaignLog({ lines, connected }: { lines: string[]; connected: boolean }) {
  const scroller = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    if (scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight;
  }, [lines.length]);
  return (
    <section className="card flex min-h-0 flex-col">
      <PanelHeader
        title="Campaign log"
        right={
          <span className={`text-[10px] ${connected ? 'text-ok' : 'text-ink-faint'}`}>
            {connected ? '● live' : '○ reconnecting'}
          </span>
        }
      />
      <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto scroll-thin px-3 py-2">
        {lines.length === 0 ? (
          <EmptyHint>waiting for campaign output…</EmptyHint>
        ) : (
          lines.map((l, i) => <LogLine key={i} line={l} />)
        )}
      </div>
    </section>
  );
}

function LogLine({ line }: { line: string }) {
  const win = line.includes('✔') || / success/.test(line);
  const fail = line.includes('✗') || /no Sim success/.test(line);
  const color = win ? '#7fa386' : fail ? '#c77b72' : undefined;
  return (
    <div className="whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed" style={color ? { color } : { color: '#b8b7af' }}>
      {line}
    </div>
  );
}
