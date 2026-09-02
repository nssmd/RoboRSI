import React from 'react';
import { Box, Text } from 'ink';
import { ROLE_COLOR, theme } from '../theme.js';
import type { Evolution } from '../api.js';
import { truncate } from '../text.js';

/**
 * Self-evolution readout, boxed: the top per-task knowledge rows (success/fail +
 * leads), the skill-library new/updated counts, and a one-line "recent skills"
 * trail. Opened on demand by `/evolution` (alias `/skills`) as a dismissible
 * overlay — it is NOT part of the always-on cockpit, which stays chat-first.
 * When there is no knowledge yet it shows a single tasteful dim line rather than
 * bare text.
 */
export function EvolutionPanel({ evolution, width }: { evolution: Evolution; width: number }) {
  const tasks = evolution.tasks.slice(0, width < 110 ? 3 : 5);
  const { skills } = evolution;
  const recent = skills.recent.slice(-3);

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={theme.border}
      paddingX={1}
      marginTop={1}
    >
      <Text bold color={theme.info}>
        🧬 evolution
      </Text>
      <Box flexDirection="column" marginTop={tasks.length ? 1 : 0}>
        {tasks.length === 0 ? (
          <Text dimColor>no accumulated task knowledge yet — the campaign will fill this in</Text>
        ) : (
          tasks.map((t) => (
            <Box key={t.task}>
              <Text color={ROLE_COLOR.engineer}>
                {truncate(t.task, Math.max(14, Math.floor(width * 0.32)))}
              </Text>
              <Text dimColor>{'  '}</Text>
              <Text color={theme.success}>{`✓${t.success}`}</Text>
              <Text dimColor>{' '}</Text>
              <Text color={t.fail ? theme.error : theme.dim}>{`✗${t.fail}`}</Text>
              {t.leads ? <Text color={ROLE_COLOR.reviewer}>{`  ${t.leads}L`}</Text> : null}
              {t.hyp_pending ? <Text color={theme.warning}>{`  ${t.hyp_pending}?`}</Text> : null}
              {t.promo_applied ? <Text color={ROLE_COLOR.manager}>{`  ↑${t.promo_applied}`}</Text> : null}
            </Box>
          ))
        )}
      </Box>
      <Box marginTop={1}>
        <Text color={ROLE_COLOR.engineer}>{`🛠 ${skills.new} new`}</Text>
        <Text dimColor>{' · '}</Text>
        <Text color={ROLE_COLOR.planner}>{`${skills.updated} updated`}</Text>
      </Box>
      {recent.length ? (
        <Text dimColor>
          {`recent · ${recent.map((s) => `${s.skill}${s.kind === 'new' ? '(+)' : '(~)'}`).join('  ')}`}
        </Text>
      ) : null}
      <Text dimColor>{'  Esc / Enter close'}</Text>
    </Box>
  );
}
