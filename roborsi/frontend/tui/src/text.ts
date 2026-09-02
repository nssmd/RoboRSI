/**
 * Tiny pure text helpers shared across the cockpit components. Standalone (no Ink,
 * no React, no imports) so any render layer can reuse them without pulling in the
 * component tree — the 复用优先 home for the `truncate` / `cap` one-liners that
 * were otherwise copy-pasted into every strip.
 */

/** Clamp `text` to `max` chars, appending an ellipsis when it had to be cut. */
export function truncate(text: string, max: number): string {
  return text.length <= max ? text : `${text.slice(0, Math.max(1, max - 1))}…`;
}

/** Capitalize the first character (role labels: "planner" → "Planner"). */
export function cap(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}
