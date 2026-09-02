import React from 'react';
import { Box, Text } from 'ink';
import { theme } from '../theme.js';
import { truncate } from '../text.js';

const HINTS = 'Enter send · talk to the Manager · /evolution skills · /proposals /approve /reject · /sessions (^P) · /exit quit';
const COMPACT_HINTS = 'Enter send · /evolution · /sessions · /exit';

/**
 * The status footer: a transient notice / health warning on the left, the live
 * connection health on the right. Falls back to the compact key hints when there
 * is nothing to say. Collapses to one truncated line on a narrow terminal.
 */
export function Footer({
  notice,
  health,
  connected,
  width,
}: {
  notice?: string;
  health?: string;
  connected: boolean;
  width: number;
}) {
  const rawLeft = notice || (health ? `⚠ ${health}` : '') || (width < 110 ? COMPACT_HINTS : HINTS);
  const right = connected ? 'connected' : 'reconnecting…';
  const rightColor = connected ? theme.success : theme.warning;
  const leftColor = notice ? theme.accent : health ? theme.warning : undefined;

  if (width < 90) {
    return (
      <Text color={leftColor} dimColor={!leftColor} wrap="truncate-end">
        {truncate(rawLeft, Math.max(12, width - 2))}
      </Text>
    );
  }
  const leftLimit = Math.max(20, width - right.length - 6);
  return (
    <Box justifyContent="space-between" width="100%">
      <Text color={leftColor} dimColor={!leftColor} wrap="truncate-end">
        {truncate(rawLeft, leftLimit)}
      </Text>
      <Text color={rightColor}>{`● ${right}`}</Text>
    </Box>
  );
}
