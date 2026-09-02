import type { Lane } from '../../lib/types';
import { PanelHeader } from '../primitives';

/**
 * One campaign daemon lane: its live "current" task/seed/round, GPU, the roster
 * it cycles through, and a strip of its most-recent rollout outcomes. Reads from
 * the lane snapshot in /api/manager (current*.txt + campaign*.log).
 */
export function LaneCard({ lane }: { lane: Lane }) {
  const live = Boolean(lane.current);
  return (
    <div className="rounded-lg border border-line-soft bg-panel-2/30">
      <PanelHeader
        title={`Lane ${lane.id} · GPU${lane.gpu}`}
        right={
          <span className={`text-[10px] ${live ? 'text-ok' : 'text-ink-faint'}`}>
            {live ? '● running' : '○ idle'}
          </span>
        }
      />
      <div className="space-y-2 px-3 py-2.5">
        <div>
          <div className="text-[9px] font-semibold uppercase tracking-wide text-ink-faint">now running</div>
          <div className="mt-0.5 truncate font-mono text-[12px] text-ink" title={lane.current || undefined}>
            {lane.current || '—'}
          </div>
        </div>
        <RecentStrip lane={lane} />
        <div className="flex items-center justify-between text-[10px] text-ink-faint">
          <span title={lane.roster.join(', ')}>{lane.roster.length} tasks in roster</span>
          <span className="font-mono tabular-nums">{lane.log_total} log lines</span>
        </div>
      </div>
    </div>
  );
}

function RecentStrip({ lane }: { lane: Lane }) {
  if (lane.recent.length === 0) {
    return <div className="text-[10px] text-ink-faint">no completed rollouts yet</div>;
  }
  // Backend yields newest-first; show oldest→newest so the tip is the latest.
  const shown = [...lane.recent].reverse();
  return (
    <div>
      <div className="text-[9px] font-semibold uppercase tracking-wide text-ink-faint">recent rollouts</div>
      <div className="mt-1 flex items-end gap-[3px]">
        {shown.map((r, i) => (
          <span
            key={`${r.task}-${r.seed}-${i}`}
            className="h-5 min-w-[8px] flex-1 rounded-sm"
            style={{ background: r.ok ? '#7fa386' : '#3d3e38' }}
            title={`${r.task} seed=${r.seed} · ${r.ok ? 'success' : 'no sim success'}`}
          />
        ))}
      </div>
    </div>
  );
}
