/**
 * Client for the RoboRSI evo dashboard (scripts/evo_dashboard.py, default port
 * 8791). Poll-only: the snapshot is fetched every 3s; the Manager front-door and
 * approve/reject are POST endpoints. Node18 global ``fetch``. ALL network logic
 * lives here so the render layer stays a thin, testable shell.
 *
 * The Snapshot types are VENDORED (no shared core import) to match the exact
 * shape the dashboard serves at ``GET /data.json``.
 */

// ── snapshot shape (mirror of scripts/evo_dashboard.py :: snapshot()) ──

export interface CampaignCurrent {
  task: string | null;
  seed: number | null;
  round: number | null;
  log: string | null;
}

export interface Campaign {
  real_success: number;
  total_runs: number;
  success_list: string[];
  current: CampaignCurrent;
  live: boolean;
}

export interface RoleView {
  role: 'planner' | 'engineer' | 'reviewer' | 'manager';
  color: string;
  active: boolean;
  action: string;
}

export interface Step {
  step: number;
  tool: string;
  args: string;
  ok: 'True' | 'False' | null;
}

export interface RunResult {
  ok: boolean;
  task: string;
  seed: number;
  tool_calls: number | null;
}

export interface EvolutionTask {
  task: string;
  success: number;
  fail: number;
  leads: number;
  hyp_pending: number;
  promo_pending: number;
  promo_applied: number;
  seed_plan?: boolean;
}

export interface RecentSkill {
  skill: string;
  kind: 'new' | 'update';
}

export interface Evolution {
  tasks: EvolutionTask[];
  skills: { new: number; updated: number; recent: RecentSkill[] };
  pending: { skill_review: number; wiki_review: number; plan_review: number };
}

/** One operator ↔ Manager turn. ``secs`` is present on Manager replies. */
export interface ConversationTurn {
  role: 'you' | 'manager';
  text: string;
  ts: string;
  secs?: number;
}

export interface Snapshot {
  generated_str: string;
  campaign: Campaign;
  roles: RoleView[];
  steps: Step[];
  reflection: string;
  runs: RunResult[];
  evolution: Evolution;
  conversation?: ConversationTurn[]; // NEW field, may be absent → []
  session?: string; // which Manager session this snapshot belongs to
  sessions?: string[]; // all known session names ("direct" always present)
  has_frame: boolean;
}

export interface MessageReply {
  reply: string;
  secs: number;
}

export interface CommandResult {
  ok: boolean;
  output: string;
}

export interface ApiOptions {
  host: string;
  port: number;
}

/** A POST endpoint the server has not shipped yet (404 / 405) — degrade, don't crash. */
export class UnsupportedEndpointError extends Error {
  constructor(readonly path: string) {
    super(`endpoint ${path} is not supported by this dashboard build`);
    this.name = 'UnsupportedEndpointError';
  }
}

/** Empty/defaulted evolution so a partial snapshot never crashes the render. */
function normalizeEvolution(e: Partial<Evolution> | undefined): Evolution {
  return {
    tasks: Array.isArray(e?.tasks) ? e!.tasks : [],
    skills: {
      new: e?.skills?.new ?? 0,
      updated: e?.skills?.updated ?? 0,
      recent: Array.isArray(e?.skills?.recent) ? e!.skills!.recent : [],
    },
    pending: {
      skill_review: e?.pending?.skill_review ?? 0,
      wiki_review: e?.pending?.wiki_review ?? 0,
      plan_review: e?.pending?.plan_review ?? 0,
    },
  };
}

/** Fill in optional/missing fields so the UI can treat a Snapshot as total. */
function normalizeSnapshot(raw: Partial<Snapshot>): Snapshot {
  return {
    generated_str: raw.generated_str ?? '',
    campaign: raw.campaign ?? {
      real_success: 0,
      total_runs: 0,
      success_list: [],
      current: { task: null, seed: null, round: null, log: null },
      live: false,
    },
    roles: Array.isArray(raw.roles) ? raw.roles : [],
    steps: Array.isArray(raw.steps) ? raw.steps : [],
    reflection: raw.reflection ?? '',
    runs: Array.isArray(raw.runs) ? raw.runs : [],
    evolution: normalizeEvolution(raw.evolution),
    conversation: Array.isArray(raw.conversation) ? raw.conversation : [],
    session: typeof raw.session === 'string' ? raw.session : undefined,
    sessions: Array.isArray(raw.sessions) ? raw.sessions : undefined,
    has_frame: raw.has_frame ?? false,
  };
}

export class ApiClient {
  readonly base: string;

  constructor(opts: ApiOptions) {
    this.base = `http://${opts.host}:${opts.port}`;
  }

  /**
   * GET /sessions — the list of known Manager session names ("direct" is always
   * present). A brand-new name only shows here after its first message, so the
   * caller may open a name that isn't in this list yet. Degrades to a safe
   * default (["direct"]) rather than crashing when the endpoint is unreachable.
   */
  async sessions(): Promise<string[]> {
    try {
      const res = await fetch(`${this.base}/sessions?_=${Date.now()}`);
      if (!res.ok) return ['direct'];
      const data = (await res.json()) as { sessions?: unknown };
      const list = Array.isArray(data.sessions) ? data.sessions.filter((s): s is string => typeof s === 'string') : [];
      return list.length > 0 ? list : ['direct'];
    } catch {
      return [];
    }
  }

  /** GET /data.json — the full board snapshot (polled every 3s). */
  async snapshot(session?: string): Promise<Snapshot> {
    const q = session ? `&session=${encodeURIComponent(session)}` : '';
    const res = await fetch(`${this.base}/data.json?_=${Date.now()}${q}`);
    if (!res.ok) throw new Error(`GET /data.json → ${res.status} ${res.statusText}`);
    const raw = (await res.json()) as Partial<Snapshot>;
    return normalizeSnapshot(raw);
  }

  /**
   * POST /message — the Manager front-door. BLOCKING: the Manager can take many
   * seconds, so callers pass a long-lived AbortSignal and show a spinner. The
   * optional ``session`` routes the turn to that persistent conversation (a new
   * name is created on first message). Throws {@link UnsupportedEndpointError}
   * if the server hasn't shipped it.
   */
  async message(text: string, session?: string, signal?: AbortSignal): Promise<MessageReply> {
    const res = await fetch(`${this.base}/message`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(session ? { text, session } : { text }),
      signal,
    });
    if (res.status === 404 || res.status === 405) throw new UnsupportedEndpointError('/message');
    if (!res.ok) throw new Error(`POST /message → ${res.status} ${res.statusText}`);
    const data = (await res.json()) as Partial<MessageReply>;
    return { reply: data.reply ?? '', secs: data.secs ?? 0 };
  }

  /**
   * POST /command — approve / reject a pending proposal by id. Throws
   * {@link UnsupportedEndpointError} if the server hasn't shipped it.
   */
  async command(cmd: 'approve' | 'reject', id: string): Promise<CommandResult> {
    const res = await fetch(`${this.base}/command`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cmd, id }),
    });
    if (res.status === 404 || res.status === 405) throw new UnsupportedEndpointError('/command');
    if (!res.ok) throw new Error(`POST /command → ${res.status} ${res.statusText}`);
    const data = (await res.json()) as Partial<CommandResult>;
    return { ok: data.ok ?? false, output: data.output ?? '' };
  }
}
