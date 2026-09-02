/**
 * Browser client for the session-cockpit webapi. URLs are relative so Vite
 * proxies /api in dev and the API serves it in prod (same origin).
 */
import type {
  CampaignFrame,
  CampaignStatus,
  EvolutionOverview,
  ManagerOverview,
  SessionRow,
  SessionTurns,
  TaskEvolution,
  TaskProgress,
  TaskTally,
} from './types';

const token = (): string | null =>
  new URLSearchParams(window.location.search).get('token') ||
  localStorage.getItem('roborsi_web_token');

function authHeaders(): Record<string, string> {
  const t = token();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

async function getJson<T>(path: string): Promise<T> {
  const r = await fetch(path, { headers: authHeaders() });
  if (!r.ok) throw new Error(`GET ${path} → ${r.status} ${r.statusText}`);
  return (await r.json()) as T;
}

export const api = {
  listSessions: () =>
    getJson<{ sessions: SessionRow[] }>('/api/sessions').then((r) => r.sessions),
  sessionTurns: (key: string) =>
    getJson<SessionTurns>(`/api/sessions/${encodeURIComponent(key)}/turns`),
  tasks: () => getJson<{ tasks: TaskTally[] }>('/api/tasks').then((r) => r.tasks),
  taskProgress: (task: string) =>
    getJson<TaskProgress>(`/api/tasks/${encodeURIComponent(task)}/progress`),
  evolution: () => getJson<EvolutionOverview>('/api/evolution'),
  manager: () => getJson<ManagerOverview>('/api/manager'),
  taskEvolution: (task: string) =>
    getJson<TaskEvolution>(`/api/tasks/${encodeURIComponent(task)}/evolution`),
  campaign: () => getJson<CampaignStatus>('/api/campaign'),
};

/** Open the live campaign stream. Returns a close() fn; auto-reconnects. */
export function openCampaignStream(
  onFrame: (f: CampaignFrame) => void,
  opts: { onOpen?: () => void; onClose?: () => void } = {},
): () => void {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const q = new URLSearchParams();
  const t = token();
  if (t) q.set('token', t);
  const url = `${proto}//${window.location.host}/api/stream?${q}`;
  let ws: WebSocket | null = null;
  let closed = false;
  let retry: ReturnType<typeof setTimeout> | undefined;
  const connect = () => {
    if (closed) return;
    ws = new WebSocket(url);
    ws.onopen = () => opts.onOpen?.();
    ws.onmessage = (e) => {
      try {
        const f = JSON.parse(e.data as string) as CampaignFrame;
        if (f && typeof f === 'object') onFrame(f);
      } catch {
        /* ignore malformed frame */
      }
    };
    ws.onclose = () => {
      opts.onClose?.();
      if (!closed) retry = setTimeout(connect, 1000);
    };
    ws.onerror = () => ws?.close();
  };
  connect();
  return () => {
    closed = true;
    if (retry) clearTimeout(retry);
    ws?.close();
  };
}
