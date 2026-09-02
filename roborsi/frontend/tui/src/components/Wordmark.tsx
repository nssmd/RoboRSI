import React from 'react';
import { Text } from 'ink';
import { theme, WORDMARK_GHOST, WORDMARK_RAMP, WORDMARK_WORD } from '../theme.js';

/**
 * The "◆ ROBORSI" gradient wordmark. The diamond rides the amber accent
 * (the Manager hue); the letters ride a steel→sky ramp coloured by absolute
 * index, so the mark reads as gold-on-steel — the same handoff-safe wordmark
 * the Header shows.
 *
 * - lit: how many letters are lit in their ramp hue (rest are ghost). Default all.
 */
export function Wordmark({ lit = WORDMARK_WORD.length }: { lit?: number }) {
  return (
    <Text>
      <Text color={theme.accent} bold>
        ◆
      </Text>
      <Text> </Text>
      {[...WORDMARK_WORD].map((ch, i) => (
        <Text key={i} color={i < lit ? WORDMARK_RAMP[i] : WORDMARK_GHOST} bold>
          {ch}
        </Text>
      ))}
    </Text>
  );
}
