import React from 'react';
import { Box, Text } from 'ink';
import { ROLE_COLOR, SPINNER, theme } from '../theme.js';
import type { RoleView, Step } from '../api.js';
import { cap, truncate } from '../text.js';

/**
 * One truthful progress line — what the triangle is doing RIGHT NOW (the argus
 * LiveActivity shape): the on-duty role's hue + spinner and the current tool
 * step, with the latest reflection folded in on a dim second line. No box, so the
 * conversation above stays the hero. Renders nothing when the triangle is idle.
 */
export function LiveActivity({
  roles,
  steps,
  reflection,
  tick,
  width,
}: {
  roles: RoleView[];
  steps: Step[];
  reflection: string;
  tick: number;
  width: number;
}) {
  const active = roles.find((r) => r.active);
  const last = steps.length ? steps[steps.length - 1] : null;
  if (!active && !last) return null;

  const hue = active ? active.color || ROLE_COLOR[active.role] || theme.info : theme.info;
  const role = active ? cap(active.role) : 'Engineer';
  const doing = last ? `${last.tool}(${truncate(last.args, 30)})` : active?.action || 'working';
  return (
    <Box marginTop={1} flexDirection="column">
      <Box>
        <Text color={hue}>{`${SPINNER[tick % SPINNER.length]} ${role} `}</Text>
        <Text wrap="truncate-end">{truncate(doing, Math.max(16, width - 28))}</Text>
        <Text dimColor>{'  · /evolution for detail'}</Text>
      </Box>
      {reflection && width >= 80 ? (
        <Text dimColor wrap="truncate-end">{`  💭 ${truncate(reflection, Math.max(20, width - 8))}`}</Text>
      ) : null}
    </Box>
  );
}
