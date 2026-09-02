import React, { useMemo } from 'react';
import { Box, Static, Text } from 'ink';
import type { ConversationTurn } from '../api.js';
import { buildEventLines, type EventLine } from '../eventLines.js';

/**
 * One conversation row. The `you ›` and `manager ▌` turns get clear visual
 * distinction: a bold coloured label in a fixed gutter, then the body. Manager
 * replies wrap; the operator's own lines stay on the accent hue. This is the
 * hero of the cockpit — the actual chat with the Manager.
 */
function Row({ line, width }: { line: EventLine; width: number }) {
  const compact = width < 80;
  const label = compact ? line.label.slice(0, 1) : line.label.padEnd(8);
  const gutter = compact ? 3 : 10;
  const bodyWidth = Math.max(12, width - gutter - 4);
  const wrapReply = line.kind === 'manager' && !compact;
  return (
    <Box>
      <Text> </Text>
      <Box width={gutter}>
        <Text color={line.labelColor} bold>
          {label}
        </Text>
      </Box>
      <Text color={line.labelColor}>{line.glyph}</Text>
      <Text> </Text>
      <Box width={bodyWidth}>
        <Text color={line.textColor} wrap={wrapReply ? 'wrap' : 'truncate-end'}>
          {line.text}
        </Text>
      </Box>
    </Box>
  );
}

/**
 * The chat feed — the operator ↔ Manager conversation, and nothing else. Every
 * turn is final on arrival, so the whole feed commits through Ink <Static> into
 * the terminal's OWN scrollback (real, unlimited scroll-up). The live triangle
 * telemetry lives in the separate one-line LiveActivity strip below, so this
 * region reads as pure chat.
 *
 * <Static> is intentionally NOT wrapped in a border box: Ink hoists its output
 * to the terminal's native scrollback, which would tear through a surrounding
 * frame.
 */
export function EventLog({ conversation, width }: { conversation: ConversationTurn[]; width: number }) {
  const lines = useMemo<EventLine[]>(() => buildEventLines(conversation), [conversation]);
  return (
    <Box flexDirection="column" marginTop={1}>
      <Static items={lines}>{(line) => <Row key={line.key} line={line} width={width} />}</Static>
      {lines.length === 0 ? (
        <Text dimColor>{'  talk to the Manager — the triangle keeps working in the background'}</Text>
      ) : null}
    </Box>
  );
}
