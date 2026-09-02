import React from 'react';
import { Box, Text } from 'ink';
import { theme } from '../theme.js';
import type { Campaign, Evolution } from '../api.js';

/**
 * Campaign KPIs as ONE flat line (the argus CostGauge analog): distinct Sim
 * successes, total runs, a live/idle dot, the current lane task+seed, and the
 * pending-review backlog. All sparse ` · `-separated text, no boxes — ambient
 * telemetry that never competes with the conversation.
 */
export function CampaignGauge({
  campaign,
  evolution,
  width,
}: {
  campaign: Campaign;
  evolution: Evolution;
  width: number;
}) {
  const p = evolution.pending;
  const pending = p.skill_review + p.wiki_review + p.plan_review;
  const cur = campaign.current.task
    ? `${campaign.current.task}${campaign.current.seed != null ? ` · seed ${campaign.current.seed}` : ''}`
    : '';
  return (
    <Box>
      <Text color={theme.success}>{`${campaign.real_success} solved`}</Text>
      <Text dimColor>{`  ·  ${campaign.total_runs} runs`}</Text>
      <Text color={campaign.live ? theme.success : theme.dim}>{campaign.live ? '  ·  ● live' : '  ·  ○ idle'}</Text>
      {cur && width >= 80 ? <Text dimColor>{`  ·  ${cur}`}</Text> : null}
      {pending ? <Text color={theme.warning}>{`  ·  ${pending} pending`}</Text> : null}
    </Box>
  );
}
