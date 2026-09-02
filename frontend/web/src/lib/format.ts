/** Small formatting helpers shared across the cockpit views. */

/** epoch-seconds → "3m ago" / "2h ago" / "just now". */
export function ago(ts: number | null | undefined): string {
  if (!ts) return '—';
  const now = Date.now() / 1000;
  const d = Math.max(0, now - ts);
  if (d < 5) return 'just now';
  if (d < 60) return `${Math.floor(d)}s ago`;
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  return `${Math.floor(d / 86400)}d ago`;
}

/** ISO timestamp → local "MM-DD HH:MM:SS" (tolerant of null). */
export function clockOf(iso: string | null | undefined): string {
  if (!iso) return '';
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return '';
  const d = new Date(ms);
  const p = (x: number) => String(x).padStart(2, '0');
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/** Restrained role hues — same language as the argus cockpit. */
const ROLE_HUE: Record<string, string> = {
  planner: '#a69daf',
  reviewer: '#b5a57f',
  env_author: '#8fa78f',
  skill_author: '#90a8b5',
  manager: '#c7a66a',
};
export const roleHue = (role: string): string => ROLE_HUE[role] ?? '#8fa7b8';

/** run status → semantic colour. */
export function statusColor(status: string, verified?: boolean): string {
  if (verified || status === 'success') return '#7fa386';
  if (status === 'running') return '#8fa7b8';
  if (status === 'error') return '#c77b72';
  return '#7e7d75'; // failed / other
}

/** Funnel/review bucket → semantic colour (pending amber, approved green,
 *  rejected red, other muted). Shared by the evolution views. */
export const BUCKET_HUE: Record<string, string> = {
  pending: '#c1a363',
  approved: '#7fa386',
  rejected: '#c77b72',
  other: '#7e7d75',
};
export const bucketHue = (bucket: string): string => BUCKET_HUE[bucket] ?? '#7e7d75';

/** 1234 → "1.2k"; small ints pass through. Keeps KPI clusters tidy. */
export function compact(n: number): string {
  if (n < 1000) return String(n);
  if (n < 10000) return `${(n / 1000).toFixed(1)}k`;
  return `${Math.round(n / 1000)}k`;
}

/** Colour for a bracketed inline marker in transcript text, or undefined for
 * plain prose. Kept next to the role/status palette so all semantic colours
 * live in one module. */
export function markerColor(text: string): string | undefined {
  const head = text.trimStart();
  if (head.startsWith('[thinking]')) return '#7e7d75';
  if (head.startsWith('[tool:')) return '#8fa78f';
  if (head.startsWith('[tool_result]')) return '#b5a57f';
  return undefined;
}
