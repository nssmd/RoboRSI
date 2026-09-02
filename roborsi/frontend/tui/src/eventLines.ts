/**
 * Pure scrollback builder for the RoboRSI cockpit conversation. Turns the
 * operator ↔ Manager turns (server turns + locally-appended optimistic ones)
 * into a bounded, KEYED list of rendered lines. No Ink, no React — testable in
 * isolation. Tool steps / reflections are NOT woven in here: they render live in
 * the one-line LiveActivity strip, so the conversation feed stays pure chat.
 *
 * Keys are content-derived and stable so Ink's <Static> commits each finished
 * turn to the terminal's native scrollback exactly once (no re-print flicker).
 */

import { theme, ROLE_COLOR } from './theme.js';
import type { ConversationTurn } from './api.js';

export type LineKind = 'you' | 'manager';

export interface EventLine {
  key: string;
  kind: LineKind;
  label: string;
  labelColor: string;
  glyph: string;
  text: string;
  textColor?: string; // undefined → default fg
}

const MAX_LINES = 400;

function turnLine(turn: ConversationTurn, idx: number): EventLine {
  const you = turn.role === 'you';
  const secs = turn.secs != null ? `  (${turn.secs.toFixed(0)}s)` : '';
  return {
    key: `turn-${idx}-${turn.role}-${turn.ts}`,
    kind: you ? 'you' : 'manager',
    label: you ? 'you' : 'manager',
    labelColor: you ? theme.accent : ROLE_COLOR.manager,
    glyph: you ? '›' : '▌',
    text: you ? turn.text : `${turn.text}${secs}`,
    textColor: you ? theme.accent : undefined,
  };
}

/** Build the conversation feed — the spine of the cockpit. Bounded to
 *  {@link MAX_LINES}, keeping the most recent turns. */
export function buildEventLines(conversation: ConversationTurn[]): EventLine[] {
  const lines = conversation.map(turnLine);
  return lines.length <= MAX_LINES ? lines : lines.slice(lines.length - MAX_LINES);
}
