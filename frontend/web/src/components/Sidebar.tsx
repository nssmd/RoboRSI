import { useMemo, useState } from 'react';
import type { ManagerTaskGroup, RoleSession } from '../lib/types';
import { ago, roleHue } from '../lib/format';
import { StatusDot, Wordmark } from './primitives';

/**
 * Left rail as the orchestration hierarchy, top-down:
 *   Manager (overview home) → Campaign & tasks · Skill evolution (views)
 *   → the task tree: each orchestrated task expands to its planner/reviewer
 *     role sessions.
 * This replaces the old flat, alphabetical session switcher: the tree mirrors
 * "look at the Manager, then what sits under it" — the Manager's task groups.
 */
export function Sidebar({
  groups,
  activeKey,
  onSelect,
  onOpenManager,
  onOpenCampaign,
  onOpenEvolution,
  managerActive,
  campaignActive,
  evolutionActive,
  loading,
  error,
}: {
  groups: ManagerTaskGroup[];
  activeKey: string | null;
  onSelect: (key: string) => void;
  onOpenManager: () => void;
  onOpenCampaign: () => void;
  onOpenEvolution: () => void;
  managerActive: boolean;
  campaignActive: boolean;
  evolutionActive: boolean;
  loading: boolean;
  error?: string;
}) {
  const [query, setQuery] = useState('');
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return groups;
    return groups.filter(
      (g) => g.task.toLowerCase().includes(q) || g.sessions.some((s) => s.role.toLowerCase().includes(q)),
    );
  }, [groups, query]);

  return (
    <aside className="flex h-full w-64 shrink-0 flex-col border-r border-line bg-surface">
      <div className="flex items-center gap-2 border-b border-line px-4 py-3.5">
        <Wordmark size={16} tag="cockpit" />
      </div>

      <nav className="mx-2 mt-2 flex flex-col gap-1">
        <NavItem active={managerActive} onClick={onOpenManager} dot="bg-manager" accent="border-manager" label="Manager" />
        <NavItem active={campaignActive} onClick={onOpenCampaign} dot="bg-blue" accent="border-blue" label="Campaign & tasks" />
        <NavItem active={evolutionActive} onClick={onOpenEvolution} dot="bg-gold" accent="border-gold" label="Skill evolution" />
      </nav>

      <div className="flex items-center justify-between px-3 pb-1 pt-3">
        <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-faint">
          Tasks under Manager
        </span>
        <span className="text-[10px] text-ink-faint">{groups.length}</span>
      </div>
      <div className="px-3 pb-2">
        <div className="flex items-center rounded border border-line bg-bg/40 px-2 focus-within:border-blue-deep">
          <span aria-hidden="true" className="mr-1.5 text-[11px] text-ink-faint">/</span>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Find task or role…"
            className="h-8 min-w-0 flex-1 bg-transparent text-xs text-ink outline-none placeholder:text-ink-faint"
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto scroll-thin px-2 pb-3">
        {loading && groups.length === 0 && <div className="px-2 py-3 text-xs text-ink-faint">loading…</div>}
        {error && <div className="px-2 py-3 text-xs text-err">overview refresh failed</div>}
        {!loading && !error && filtered.length === 0 && (
          <div className="px-2 py-3 text-xs text-ink-faint">no tasks match</div>
        )}
        {filtered.map((g) => (
          <TaskTreeNode key={g.task} group={g} activeKey={activeKey} onSelect={onSelect} />
        ))}
      </div>
    </aside>
  );
}

function NavItem({
  active,
  onClick,
  dot,
  accent,
  label,
}: {
  active: boolean;
  onClick: () => void;
  dot: string;
  accent: string;
  label: string;
}) {
  return (
    <button
      onClick={onClick}
      aria-current={active ? 'page' : undefined}
      className={`flex items-center gap-2 rounded border-l-2 px-2.5 py-2 text-left text-sm transition-colors ${
        active ? `${accent} bg-panel text-ink` : 'border-transparent text-ink-dim hover:bg-panel/60'
      }`}
    >
      <span className={`inline-block h-1.5 w-1.5 rounded-full ${dot}`} />
      {label}
    </button>
  );
}

/** A collapsible task node: the task header (solved marker + leads), expanding
 *  to its planner/reviewer role sessions. Auto-opens when a child is active. */
function TaskTreeNode({
  group,
  activeKey,
  onSelect,
}: {
  group: ManagerTaskGroup;
  activeKey: string | null;
  onSelect: (key: string) => void;
}) {
  const hasActive = group.sessions.some((s) => s.key === activeKey);
  const [open, setOpen] = useState(hasActive);
  const expanded = open || hasActive;
  return (
    <div className="mb-1">
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={expanded}
        className="row-hover flex w-full items-center gap-1.5 rounded px-2 py-1 text-left"
      >
        <span className={`text-[9px] text-ink-faint transition-transform ${expanded ? 'rotate-90' : ''}`}>▸</span>
        <StatusDot ok={group.verified_success > 0} title={group.verified_success > 0 ? 'verified success' : 'unsolved'} />
        <span className="min-w-0 flex-1 truncate text-[11px] font-medium text-ink-dim" title={group.task}>
          {group.task}
        </span>
        {group.leads > 0 && <span className="shrink-0 text-[9px] text-gold-soft" title="approved leads">{group.leads}L</span>}
      </button>
      {expanded && group.sessions.map((s) => (
        <RoleLeaf key={s.key} session={s} active={s.key === activeKey} onSelect={onSelect} />
      ))}
    </div>
  );
}

function RoleLeaf({
  session,
  active,
  onSelect,
}: {
  session: RoleSession;
  active: boolean;
  onSelect: (key: string) => void;
}) {
  const hue = roleHue(session.role);
  return (
    <button
      onClick={() => onSelect(session.key)}
      aria-current={active ? 'page' : undefined}
      title={session.thread_id}
      className={`group ml-4 flex w-[calc(100%-1rem)] items-center gap-2 border-l-2 px-2.5 py-1.5 text-left transition-colors ${
        active ? 'border-blue bg-panel' : 'border-transparent hover:bg-panel/60'
      }`}
    >
      <span className="inline-block h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: hue }} />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-xs font-medium capitalize" style={{ color: active ? hue : '#b8b7af' }}>
          {session.role || 'session'}
        </span>
        <span className="block text-[10px] text-ink-faint">
          {session.has_transcript ? ago(session.last_active) : 'no transcript'}
        </span>
      </span>
    </button>
  );
}
