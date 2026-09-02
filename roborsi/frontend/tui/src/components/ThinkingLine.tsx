import React from 'react';
import { Box, Text } from 'ink';
import { ROLE_COLOR, SPINNER, theme } from '../theme.js';

/**
 * The live "manager 思考中…" line, shown only while a message is in flight — a
 * spinner in the manager hue + elapsed seconds so the terminal never looks
 * frozen while the Manager (which can take many seconds) works.
 */
export function ThinkingLine({ tick, elapsedS }: { tick: number; elapsedS: number }) {
  const spin = SPINNER[tick % SPINNER.length];
  return (
    <Box flexDirection="column" marginTop={1}>
      <Text wrap="truncate-end">
        {'  '}
        <Text color={ROLE_COLOR.manager}>{spin}</Text>
        {' '}
        <Text color={ROLE_COLOR.manager} bold>
          manager
        </Text>
        <Text color={theme.accent}>{' 思考中…'}</Text>
        <Text dimColor>{`   ${elapsedS}s`}</Text>
      </Text>
      <Text dimColor>{'  Esc / Ctrl-C to leave · the reply is still being computed'}</Text>
    </Box>
  );
}
