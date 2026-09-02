/** Shared types for the RoboRSI session cockpit — mirror the webapi shapes
 *  in roborsi/webapi/cockpit_data.py. */

export interface SessionRow {
  key: string; // "role:task"
  role: string;
  task: string;
  thread_id: string;
  last_active: number | null; // transcript mtime (epoch seconds)
  task_success: boolean; // task has a predicate-verified success
  success_count: number;
  has_transcript: boolean;
}

export interface Turn {
  role: 'user' | 'assistant';
  text: string;
  ts: string | null; // ISO timestamp
}

export interface SessionTurns {
  found: boolean;
  key: string;
  role: string;
  task: string;
  thread_id: string;
  transcript_path: string | null;
  turns: Turn[];
}

export interface TaskTally {
  task: string;
  total: number;
  verified_success: number;
}

export interface RunRow {
  id: string;
  seed: number | null;
  status: string;
  outcome: string | null;
  verified: boolean;
  started_at: string | null;
  finished_at: string | null;
  wallclock_s: number | null;
}

export interface TaskProgress {
  task: string;
  total_runs: number;
  shown: number;
  verified_success: number;
  recent: RunRow[];
}

export interface CampaignStatus {
  current: string;
  current_b: string;
  log_total: number;
  recent_lines: string[];
  lanes: Lane[];
}

/** One campaign daemon lane (A=GPU1 primary, B=GPU0). */
export interface Lane {
  id: string; // "A" | "B"
  gpu: string; // "1" | "0"
  current: string; // "adjust_bottle seed=1 round=1"
  roster: string[]; // tasks this lane cycles through
  recent: LaneRollout[]; // recent completed rollouts, newest first
  log_total: number;
}

export interface LaneRollout {
  task: string;
  seed: number;
  ok: boolean;
}

/** WS frame from /api/stream: a seed (initial tail) then append deltas. */
export interface CampaignFrame {
  type: 'seed' | 'append';
  lines: string[];
  next_offset: number;
  total: number;
}

/* --------------------------------------------------------- self-evolution */

/** One task's knowledge-accretion row in the global evolution overview. */
export interface EvolutionTask {
  task: string;
  leads: number; // Manager-approved leads count
  success_traces: number; // wiki "outcome: ✓ success" count
  fail_traces: number; // wiki "outcome: ✗ failure" count
  verified_success: number; // predicate-verified successes (trace.db)
  hyp_pending: number;
  hyp_approved: number;
  hyp_rejected: number;
}

export interface FunnelTally {
  pending: number;
  approved: number;
  rejected: number;
  other: number;
  total: number;
}

export interface EvolutionOverview {
  tasks: EvolutionTask[];
  totals: Record<string, FunnelTally>; // keyed by queue name
  task_count: number;
}

/** A single Manager-approved lead parsed from a task's wiki.md. */
export interface Lead {
  run_id: string;
  text: string;
  root_cause: string;
  approved: string; // "<iso> · <manager note>"
}

export interface Hypothesis {
  id: string | null;
  status: string | null;
  bucket: 'pending' | 'approved' | 'rejected' | 'other';
  root_cause: string;
  next_action: string;
  manager_note: string;
  created_at: string | null;
}

export interface TrendPoint {
  id: string;
  seed: number | null;
  status: string;
  outcome: string | null;
  verified: boolean;
  started_at: string | null;
}

export interface TaskEvolution {
  task: string;
  has_wiki: boolean;
  leads: Lead[];
  measurements: string;
  successful_traces: string;
  success_traces: number;
  fail_traces: number;
  hypotheses: Hypothesis[];
  hyp_funnel: { pending: number; approved: number; rejected: number };
  trend: TrendPoint[];
}

/* ------------------------------------------------------- Manager overview */

/** A real manager session (backend-agnostic) + its recent activity. Discovered
 *  via roborsi.agents.manager.sessions (same list the CLI picker uses). */
export interface ManagerSession {
  id: string;
  backend: string; // claude | codex | copilot
  topic: string; // robotwin | libero | other
  label: string;
  last_active: number | null;
  turn_count: number;
  recent_turns: Turn[];
}

export interface ManagerTotals {
  verified_success: number;
  pending_review: number;
  pending_by_queue: Record<string, number>;
  orchestrated_tasks: number;
}

/** A per-task role session (planner/reviewer) under the Manager. Lighter than
 *  SessionRow — no task-level success fields (those live on the group). */
export interface RoleSession {
  key: string;
  role: string;
  task: string;
  thread_id: string;
  last_active: number | null;
  has_transcript: boolean;
}

/** A task the Manager orchestrates, with its planner/reviewer role sessions. */
export interface ManagerTaskGroup {
  task: string;
  sessions: RoleSession[];
  verified_success: number;
  leads: number;
  last_active: number | null;
}

export interface ManagerOverview {
  managers: ManagerSession[];
  totals: ManagerTotals;
  lanes: Lane[];
  task_groups: ManagerTaskGroup[];
}
