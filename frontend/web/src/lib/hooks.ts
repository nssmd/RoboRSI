import { useQuery } from '@tanstack/react-query';
import { useEffect, useReducer, useState } from 'react';
import { api, openCampaignStream } from './api';
import type { CampaignFrame } from './types';

export const useSessions = () =>
  useQuery({ queryKey: ['sessions'], queryFn: api.listSessions, refetchInterval: 5_000 });

export const useSessionTurns = (key: string | null) =>
  useQuery({
    queryKey: ['turns', key],
    queryFn: () => api.sessionTurns(key!),
    enabled: !!key,
    refetchInterval: 8_000,
  });

export const useTasks = () =>
  useQuery({ queryKey: ['tasks'], queryFn: api.tasks, refetchInterval: 6_000 });

export const useTaskProgress = (task: string | null) =>
  useQuery({
    queryKey: ['progress', task],
    queryFn: () => api.taskProgress(task!),
    enabled: !!task,
    refetchInterval: 6_000,
  });

export const useCampaign = () =>
  useQuery({ queryKey: ['campaign'], queryFn: api.campaign, refetchInterval: 4_000 });

export const useEvolution = () =>
  useQuery({ queryKey: ['evolution'], queryFn: api.evolution, refetchInterval: 8_000 });

export const useManager = () =>
  useQuery({ queryKey: ['manager'], queryFn: api.manager, refetchInterval: 5_000 });

export const useTaskEvolution = (task: string | null) =>
  useQuery({
    queryKey: ['task-evolution', task],
    queryFn: () => api.taskEvolution(task!),
    enabled: !!task,
    refetchInterval: 8_000,
  });

/* ------------------------------------------------ live campaign log stream */

const MAX_LINES = 2_000;

type LogState = { lines: string[]; offset: number };
type LogAction = { kind: 'reset' } | { kind: 'frame'; frame: CampaignFrame };

function logReducer(state: LogState, action: LogAction): LogState {
  if (action.kind === 'reset') return { lines: [], offset: 0 };
  const f = action.frame;
  if (f.type === 'seed') return { lines: f.lines.slice(-MAX_LINES), offset: f.next_offset };
  // append — only accept forward deltas so a reconnect backfill never doubles
  if (f.next_offset <= state.offset) return state;
  const merged = [...state.lines, ...f.lines];
  return { lines: merged.slice(-MAX_LINES), offset: f.next_offset };
}

export interface CampaignStreamHandle {
  lines: string[];
  connected: boolean;
}

/** Subscribe to the live campaign.log feed over WS with auto-reconnect. */
export function useCampaignStream(): CampaignStreamHandle {
  const [state, dispatch] = useReducer(logReducer, { lines: [], offset: 0 });
  const [connected, setConnected] = useState(false);
  useEffect(() => {
    dispatch({ kind: 'reset' });
    const close = openCampaignStream((frame) => dispatch({ kind: 'frame', frame }), {
      onOpen: () => setConnected(true),
      onClose: () => setConnected(false),
    });
    return close;
  }, []);
  return { lines: state.lines, connected };
}
