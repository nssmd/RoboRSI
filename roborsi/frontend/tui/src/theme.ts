/**
 * Visual DNA for the RoboRSI cockpit. Role hues match the Python dashboard
 * (scripts/evo_dashboard.py ROLE_COLOR) EXACTLY so the TUI and the web board read
 * the same: planner blue, engineer green, reviewer violet, manager amber.
 */

/** Role → hex, byte-identical to the evo_dashboard palette. */
export const ROLE_COLOR: Record<string, string> = {
  planner: '#2f6df0', // blue
  engineer: '#27a567', // green
  reviewer: '#7c5cff', // violet
  manager: '#e0930f', // amber
};

/** Semantic palette — a slate/steel board with an amber accent (the Manager hue). */
export const theme = {
  accent: '#e0930f', // amber — the Manager mark / input glyph
  border: '#243350', // cool slate line (dashboard --line)
  success: '#27a567', // green
  error: '#e0544e', // red (dashboard --red)
  warning: '#e0930f', // amber
  info: '#2f6df0', // blue
  muted: '#8fa3c4', // dashboard --muted
  dim: '#5f6f8f', // dashboard --dim
  role: ROLE_COLOR,
};

/** Braille spinner frames — the same cadence as the argus cockpit / Codex CLI. */
export const SPINNER = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];

/**
 * ROBORSI wordmark — a steel→sky gradient across the 10 letters, coloured by
 * absolute index (R deep steel … S sky). RAMP is the lit ramp; GHOST an unlit
 * letter; the diamond mark rides the amber accent. Shared by the Header so the
 * "◆ ROBORSI" mark reads as gold-on-steel.
 */
const WORD = 'ROBORSI';
export const WORDMARK_RAMP: string[] = (() => {
  // 10-stop steel→sky ramp; interpolate two anchor hues for a smooth gradient.
  const from = [0x3b, 0x5f, 0xc4]; // steel
  const to = [0x72, 0xc4, 0xf0]; // sky
  const n = WORD.length;
  const hex = (v: number) => v.toString(16).padStart(2, '0');
  return Array.from({ length: n }, (_, i) => {
    const t = n === 1 ? 0 : i / (n - 1);
    const c = from.map((f, k) => Math.round(f + (to[k] - f) * t));
    return `#${hex(c[0])}${hex(c[1])}${hex(c[2])}`;
  });
})();
export const WORDMARK_GHOST = '#48506b';
export const WORDMARK_WORD = WORD;
