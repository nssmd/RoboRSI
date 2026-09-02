/**
 * Slash-command registry + completion + dispatch parsing for the RoboRSI
 * cockpit. Pure logic (no Ink) so it stays testable. Adapted from the argus
 * cockpit's slash helper, trimmed to the RoboRSI command set.
 */

export interface SlashCmd {
  name: string; // canonical, e.g. "/approve"
  arg?: string; // usage hint
  desc: string;
  aliases?: string[];
  group: string; // for grouped /help
}

export const SLASH_COMMANDS: SlashCmd[] = [
  // ── cockpit ──
  { name: '/status', desc: 'flash the campaign KPI bar (state is already live)', group: 'Cockpit' },
  { name: '/evolution', desc: 'open the self-evolution readout (skills / task knowledge)', aliases: ['/skills'], group: 'Cockpit' },
  { name: '/proposals', desc: 'show the pending review queues (skill / wiki / plan)', aliases: ['/pending', '/queue'], group: 'Cockpit' },
  { name: '/sessions', desc: 'switch Manager session (back to the picker)', aliases: ['/switch'], group: 'Cockpit' },
  { name: '/help', desc: 'keys + full command reference', aliases: ['/?', '/commands'], group: 'Cockpit' },
  // ── review gate (actions) ──
  { name: '/approve', arg: '<id>', desc: 'approve a pending proposal by id', group: 'Review gate' },
  { name: '/reject', arg: '<id>', desc: 'reject a pending proposal by id', group: 'Review gate' },
  // ── other (local) ──
  { name: '/clear', desc: 'clear the event feed view', group: 'Other' },
  { name: '/quit', desc: 'leave the cockpit (the campaign keeps running)', aliases: ['/exit', '/q'], group: 'Other' },
];

const CANON = new Map<string, SlashCmd>();
for (const c of SLASH_COMMANDS) {
  for (const n of [c.name, ...(c.aliases ?? [])]) CANON.set(n.toLowerCase(), c);
}

export function isSlash(line: string): boolean {
  return line.startsWith('/');
}

/** Completions while typing the command TOKEN (before the first space). */
export function slashCompletions(line: string): SlashCmd[] {
  if (!isSlash(line) || line.includes(' ')) return [];
  const token = line.toLowerCase();
  const seen = new Set<string>();
  const out: SlashCmd[] = [];
  for (const c of SLASH_COMMANDS) {
    const names = [c.name, ...(c.aliases ?? [])];
    if (names.some((n) => n.toLowerCase().startsWith(token)) && !seen.has(c.name)) {
      seen.add(c.name);
      out.push(c);
    }
  }
  // An exactly-typed command must not lose Enter to a prefix sibling.
  return out.sort((a, b) => Number(isExact(b, token)) - Number(isExact(a, token)));
}

function isExact(command: SlashCmd, token: string): boolean {
  return [command.name, ...(command.aliases ?? [])].some((name) => name.toLowerCase() === token);
}

export function applyCompletion(cmd: SlashCmd): string {
  return cmd.arg ? `${cmd.name} ` : cmd.name;
}

export interface ParsedCommand {
  cmd: SlashCmd | null; // null → unknown
  name: string; // canonical (or the typed token if unknown)
  rest: string;
}

export function parseCommand(line: string): ParsedCommand | null {
  if (!isSlash(line)) return null;
  const sp = line.indexOf(' ');
  const token = (sp === -1 ? line : line.slice(0, sp)).toLowerCase();
  const rest = sp === -1 ? '' : line.slice(sp + 1).trim();
  const cmd = CANON.get(token) ?? null;
  return { cmd, name: cmd ? cmd.name : token, rest };
}

/** difflib-style "did you mean /x?" for an unknown command token. */
export function didYouMean(token: string): string | null {
  const t = token.toLowerCase();
  let best: string | null = null;
  let bestScore = 0;
  for (const name of CANON.keys()) {
    const s = similarity(t, name);
    if (s > bestScore) {
      bestScore = s;
      best = CANON.get(name)!.name;
    }
  }
  return bestScore >= 0.6 ? best : null;
}

/** Ratcliff/Obershelp-ish ratio via normalized edit distance. */
function similarity(a: string, b: string): number {
  const d = levenshtein(a, b);
  const max = Math.max(a.length, b.length) || 1;
  return 1 - d / max;
}

function levenshtein(a: string, b: string): number {
  const m = a.length;
  const n = b.length;
  const dp = Array.from({ length: m + 1 }, (_, i) => [i, ...Array(n).fill(0)]);
  for (let j = 0; j <= n; j++) dp[0][j] = j;
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      dp[i][j] = Math.min(
        dp[i - 1][j] + 1,
        dp[i][j - 1] + 1,
        dp[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1),
      );
    }
  }
  return dp[m][n];
}

/** Grouped view for /help, with aliases folded ('/quit  (= /exit, /q)'). */
export function helpGroups(): Array<{ group: string; rows: Array<{ label: string; desc: string }> }> {
  const order = ['Cockpit', 'Review gate', 'Other'];
  const groups = new Map<string, Array<{ label: string; desc: string }>>();
  for (const c of SLASH_COMMANDS) {
    const aliasNote = c.aliases?.length ? `  (= ${c.aliases.join(', ')})` : '';
    const label = `${c.name}${c.arg ? ` ${c.arg}` : ''}${aliasNote}`;
    if (!groups.has(c.group)) groups.set(c.group, []);
    groups.get(c.group)!.push({ label, desc: c.desc });
  }
  return order.filter((g) => groups.has(g)).map((g) => ({ group: g, rows: groups.get(g)! }));
}
