import React from 'react';
import { Box, Text } from 'ink';
import { ROLE_COLOR, theme } from '../theme.js';
import type { RoleView } from '../api.js';
import { cap, truncate } from '../text.js';

const ORDER: RoleView['role'][] = ['manager', 'planner', 'engineer', 'reviewer'];

/**
 * The triangle at a glance — Manager / Planner / Engineer / Reviewer as ONE flat
 * strip (the argus RolesBar shape): a hue-lit ● for the on-duty role, a dim ○ for
 * the idle ones, and a short action after whoever is active. No boxes — a single
 * sparse line so it reads as ambient context under the conversation. Collapses to
 * just the active role under ~90 cols.
 */
export function RolesBar({ roles, width }: { roles: RoleView[]; width: number }) {
  const byRole = new Map(roles.map((r) => [r.role, r]));
  if (width < 90) {
    const active = roles.find((r) => r.active);
    return active ? (
      <Box>
        <Text color={active.color || ROLE_COLOR[active.role] || 'white'}>{`● ${cap(active.role)}`}</Text>
        <Text dimColor>{` · ${truncate(active.action || 'active', Math.max(10, width - 16))}`}</Text>
      </Box>
    ) : (
      <Text dimColor>triangle · all idle</Text>
    );
  }
  return (
    <Box gap={3}>
      {ORDER.map((name) => {
        const r = byRole.get(name);
        const hue = ROLE_COLOR[name] ?? 'white';
        const active = !!r?.active;
        return (
          <Box key={name}>
            <Text color={active ? hue : theme.dim}>{active ? '●' : '○'}</Text>
            <Text> </Text>
            <Text color={active ? hue : theme.dim} bold={active}>
              {cap(name)}
            </Text>
            {active && r?.action ? <Text dimColor>{` ${truncate(r.action, 26)}`}</Text> : null}
          </Box>
        );
      })}
    </Box>
  );
}
