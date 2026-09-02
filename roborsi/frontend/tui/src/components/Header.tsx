import React from 'react';
import { Box, Text } from 'ink';
import { theme } from '../theme.js';
import type { Snapshot } from '../api.js';
import { truncate } from '../text.js';
import { Wordmark } from './Wordmark.js';

/**
 * The cockpit header: the "◆ ROBORSI" gradient wordmark on the left with a
 * clean status line — a connection dot (green connected / amber connecting), a
 * live/idle campaign badge, and the dashboard's generated_str right-aligned.
 * Collapses gracefully under ~78 cols (drops the timestamp, folds health onto
 * its own line).
 */
export function Header({
  snap,
  connected,
  width,
  health = '',
  session = '',
}: {
  snap: Snapshot | null;
  connected: boolean;
  width: number;
  health?: string;
  session?: string;
}) {
  const live = snap?.campaign.live ?? false;
  const link = connected
    ? { color: theme.success, text: 'connected' }
    : { color: theme.warning, text: 'connecting…' };
  const compact = width < 78;
  const stamp = snap?.generated_str ? `updated ${snap.generated_str}` : '';

  return (
    <Box flexDirection="column">
      <Box>
        <Box flexGrow={1}>
          <Wordmark />
          {session ? (
            <>
              <Text> </Text>
              <Text color="cyan">{truncate(session, compact ? 14 : 28)}</Text>
            </>
          ) : null}
          <Text dimColor>{'  ·  '}</Text>
          <Text color={live ? theme.success : theme.dim}>{live ? '● live' : '○ idle'}</Text>
          <Text dimColor>{'  ·  '}</Text>
          <Text color={link.color}>{`● ${link.text}`}</Text>
        </Box>
        {!compact && stamp ? <Text dimColor>{stamp}</Text> : null}
      </Box>
      {health ? (
        <Text color={theme.warning}>{`  ⚠ ${truncate(health, Math.max(12, width - 6))}`}</Text>
      ) : null}
    </Box>
  );
}
