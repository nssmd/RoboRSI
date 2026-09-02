import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Box, Text, useApp, useInput, useStdout } from 'ink';
import { ApiClient, UnsupportedEndpointError, type ConversationTurn, type Snapshot } from './api.js';
import {
  backspace,
  deleteWordBefore,
  EMPTY,
  end,
  fromString,
  home,
  insert,
  killToEnd,
  killToStart,
  left,
  right,
  type Edit,
} from './input/editor.js';
import { EMPTY_HISTORY, newer, older, remember, type History } from './input/history.js';
import { applyCompletion, didYouMean, helpGroups, isSlash, parseCommand, slashCompletions } from './input/slash.js';
import { consumePasteChunk } from './input/paste.js';
import { useTerminalSize } from './useTerminalSize.js';
import { Header } from './components/Header.js';
import { RolesBar } from './components/RolesBar.js';
import { CampaignGauge } from './components/CampaignGauge.js';
import { EvolutionPanel } from './components/EvolutionPanel.js';
import { EventLog } from './components/EventLog.js';
import { LiveActivity } from './components/LiveActivity.js';
import { ThinkingLine } from './components/ThinkingLine.js';
import { PromptBox } from './components/PromptBox.js';
import { SlashMenu } from './components/SlashMenu.js';
import { Footer } from './components/Footer.js';
import { SessionPicker } from './components/SessionPicker.js';

const POLL_MS = 3000;
const TICK_MS = 120;
const DEFAULT_SESSION = 'direct';

export interface AppProps {
  host: string;
  port: number;
  /** When set, skip the picker and open this session's cockpit directly. */
  session?: string;
}

/** Wall-clock HH:MM:SS stamp for a locally-appended (optimistic) turn. */
function nowTs(): string {
  return new Date().toISOString().slice(11, 19);
}

export function App({ host, port, session: initialSession }: AppProps) {
  const { exit } = useApp();
  const { stdout } = useStdout();
  const terminal = useTerminalSize();
  const api = useMemo(() => new ApiClient({ host, port }), [host, port]);

  // Session state machine: launch into the picker unless a session was passed
  // on the CLI (--session), in which case go straight to that cockpit.
  const [mode, setMode] = useState<'picker' | 'cockpit'>(initialSession ? 'cockpit' : 'picker');
  const [session, setSession] = useState<string>(initialSession ?? DEFAULT_SESSION);
  const [sessions, setSessions] = useState<string[]>([DEFAULT_SESSION]);

  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [connected, setConnected] = useState(false);
  const [notice, setNotice] = useState('');
  const [edit, setEdit] = useState<Edit>(EMPTY);
  const [history, setHistory] = useState<History>(EMPTY_HISTORY);
  const [menuSel, setMenuSel] = useState(0);
  const [pendingExit, setPendingExit] = useState(false);
  // A one-shot overlay opened by a slash command (currently only the evolution
  // readout). null → the clean chat-first cockpit; dismissed with Esc / Enter.
  const [panel, setPanel] = useState<'evolution' | null>(null);

  // Locally-appended conversation (optimistic ``you`` turns + Manager replies)
  // shown ahead of / alongside whatever the snapshot carries. The server's
  // conversation is authoritative when present; these bridge the poll gap.
  const [localTurns, setLocalTurns] = useState<ConversationTurn[]>([]);

  // A Manager turn in flight → drive the thinking indicator.
  const [pending, setPending] = useState(false);
  const [startedAt, setStartedAt] = useState(0);
  const [tick, setTick] = useState(0);

  const pasteActiveRef = useRef(false);
  const managerControllerRef = useRef<AbortController | null>(null);

  // Bracketed-paste on/off for the terminal.
  useEffect(() => {
    if (!stdout.isTTY) return;
    stdout.write('\u001b[?2004h');
    return () => {
      stdout.write('\u001b[?2004l');
    };
  }, [stdout]);

  // In the picker: fetch the session list once (and refresh whenever we return
  // to it). Degrades to ["direct"] on a dead dashboard so the picker still opens.
  useEffect(() => {
    if (mode !== 'picker') return;
    let alive = true;
    (async () => {
      const list = await api.sessions();
      if (!alive) return;
      setSessions(list.length > 0 ? list : [DEFAULT_SESSION]);
      setConnected(list.length > 0);
    })();
    return () => {
      alive = false;
    };
  }, [api, mode]);

  // Poll the chosen session's snapshot every 3s (cockpit only); a failure flips
  // connected=false but keeps polling. Reset the board on a session switch so
  // stale conversation never bleeds across.
  useEffect(() => {
    if (mode !== 'cockpit') return;
    let alive = true;
    setSnap(null);
    setLocalTurns([]);
    const poll = async () => {
      try {
        const s = await api.snapshot(session);
        if (alive) {
          setSnap(s);
          setConnected(true);
          if (Array.isArray(s.sessions) && s.sessions.length > 0) setSessions(s.sessions);
        }
      } catch {
        if (alive) setConnected(false);
      }
    };
    poll();
    const id = setInterval(poll, POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [api, mode, session]);

  // Ticker for the thinking spinner + elapsed clock (only while pending).
  useEffect(() => {
    if (!pending) return;
    const id = setInterval(() => setTick((t) => t + 1), TICK_MS);
    return () => clearInterval(id);
  }, [pending]);

  // A slow ambient tick so the active-role spinner animates even when idle-typing.
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 200);
    return () => clearInterval(id);
  }, []);

  useEffect(
    () => () => {
      managerControllerRef.current?.abort();
    },
    [],
  );

  const quit = () => {
    managerControllerRef.current?.abort();
    exit();
  };

  // Return to the picker to switch sessions (any in-flight Manager turn is
  // abandoned — the campaign keeps running server-side).
  const openPicker = () => {
    managerControllerRef.current?.abort();
    managerControllerRef.current = null;
    setPending(false);
    setPanel(null);
    setNotice('');
    setMode('picker');
  };

  // Chosen from the picker (existing name or a freshly-typed one) → open its
  // cockpit. A brand-new name just works; its conversation is created server-
  // side on the first message.
  const openSession = (name: string) => {
    setSession(name);
    setNotice('');
    setMode('cockpit');
  };

  // The full conversation to render: server turns first (authoritative), then
  // any local turns the server hasn't echoed back yet (deduped by text+role).
  const conversation = useMemo<ConversationTurn[]>(() => {
    const server = snap?.conversation ?? [];
    if (localTurns.length === 0) return server;
    const seen = new Set(server.map((t) => `${t.role}::${t.text}`));
    const extra = localTurns.filter((t) => !seen.has(`${t.role}::${t.text}`));
    return [...server, ...extra];
  }, [snap, localTurns]);

  const submitFreeText = async (text: string) => {
    if (managerControllerRef.current) {
      setNotice('the Manager is still thinking · wait for the reply');
      return;
    }
    const controller = new AbortController();
    managerControllerRef.current = controller;
    setLocalTurns((prev) => [...prev, { role: 'you', text, ts: nowTs() }]);
    setStartedAt(Date.now());
    setTick(0);
    setPending(true);
    setNotice('');
    try {
      const { reply, secs } = await api.message(text, session, controller.signal);
      if (!controller.signal.aborted) {
        setLocalTurns((prev) => [...prev, { role: 'manager', text: reply || '(no reply)', ts: nowTs(), secs }]);
      }
    } catch (error) {
      if (controller.signal.aborted) return;
      if (error instanceof UnsupportedEndpointError) {
        setNotice('this dashboard build has no /message endpoint — chat unsupported');
        setLocalTurns((prev) => [
          ...prev,
          { role: 'manager', text: '(chat unsupported by this dashboard build)', ts: nowTs() },
        ]);
      } else {
        setNotice(`couldn’t reach the Manager: ${(error as Error).message}`);
      }
    } finally {
      if (managerControllerRef.current === controller) {
        managerControllerRef.current = null;
        setPending(false);
      }
    }
  };

  const runCommand = async (cmd: 'approve' | 'reject', id: string) => {
    try {
      const { ok, output } = await api.command(cmd, id);
      setNotice(`${cmd} ${id} · ${ok ? 'ok' : 'failed'}${output ? ` · ${output.slice(0, 80)}` : ''}`);
    } catch (error) {
      if (error instanceof UnsupportedEndpointError) {
        setNotice('this dashboard build has no /command endpoint — approve/reject unsupported');
      } else {
        setNotice(`${cmd} failed: ${(error as Error).message}`);
      }
    }
  };

  const proposalsSummary = (): string => {
    const p = snap?.evolution.pending;
    if (!p) return 'proposals · loading…';
    const total = p.skill_review + p.wiki_review + p.plan_review;
    if (total === 0) return 'proposals · queues empty';
    return `proposals · skill ${p.skill_review} · wiki ${p.wiki_review} · plan ${p.plan_review} — /approve <id> or /reject <id>`;
  };

  const dispatchSlash = (line: string) => {
    const p = parseCommand(line);
    if (!p) return;
    if (!p.cmd) {
      const s = didYouMean(p.name);
      setNotice(s ? `unknown ${p.name} — did you mean ${s}?` : `unknown command ${p.name} — /help`);
      return;
    }
    const need = (usage: string) => setNotice(`usage: ${usage}`);
    switch (p.cmd.name) {
      case '/help': {
        const groups = helpGroups();
        setNotice(groups.map((g) => g.rows.map((r) => r.label.split('  ')[0]).join(' ')).join('  ·  '));
        break;
      }
      case '/status':
        setNotice(
          snap
            ? `campaign · ${snap.campaign.real_success} solved · ${snap.campaign.total_runs} runs · ${snap.campaign.live ? 'live' : 'idle'}`
            : 'campaign · loading…',
        );
        break;
      case '/proposals':
        setNotice(proposalsSummary());
        break;
      case '/evolution':
        setPanel('evolution');
        setNotice('');
        break;
      case '/sessions':
        openPicker();
        break;
      case '/approve':
        if (p.rest) void runCommand('approve', p.rest);
        else need('/approve <id>');
        break;
      case '/reject':
        if (p.rest) void runCommand('reject', p.rest);
        else need('/reject <id>');
        break;
      case '/clear':
        setLocalTurns([]);
        setNotice('local feed cleared (server conversation still shown)');
        break;
      case '/quit':
        quit();
        break;
      default:
        setNotice(`${p.cmd.name} not wired`);
    }
  };

  const submit = () => {
    const text = edit.value.trim();
    if (!text) return;
    if (!isSlash(text) && managerControllerRef.current) {
      setNotice('the Manager is still thinking · wait for the reply');
      return;
    }
    setEdit(EMPTY);
    setMenuSel(0);
    setHistory((h) => remember(h, text));
    if (isSlash(text)) dispatchSlash(text);
    else void submitFreeText(text);
  };

  useInput((input, key) => {
    // Bracketed / multi-char paste → funnel straight into the editor.
    const paste = consumePasteChunk(input, pasteActiveRef.current);
    if (paste.handled) {
      pasteActiveRef.current = paste.active;
      if (paste.text) {
        setEdit((current) => insert(current, paste.text));
        setHistory((current) => (current.pos === 0 ? current : { ...current, pos: 0 }));
      }
      return;
    }

    if (key.ctrl && input === 'c') {
      if (pendingExit) {
        quit();
        return;
      }
      setPendingExit(true);
      setNotice('Ctrl-C again to exit · Ctrl-D also quits · the campaign keeps running');
      return;
    }
    if (key.ctrl && input === 'd') {
      quit();
      return;
    }
    if (key.ctrl && input === 'p') {
      openPicker();
      return;
    }
    if (pendingExit) setPendingExit(false); // any other key disarms the double Ctrl-C

    // An open overlay (e.g. /evolution) is modal: Esc / Enter / q dismisses it
    // and swallows the key so it never leaks into the prompt.
    if (panel) {
      if (key.escape || key.return || input === 'q') setPanel(null);
      return;
    }

    const comps = slashCompletions(edit.value);
    const menuOpen = comps.length > 0;

    if (key.escape) {
      if (menuOpen) setEdit(EMPTY);
      return;
    }
    if (menuOpen) {
      if (key.upArrow) {
        setMenuSel((s) => (s - 1 + comps.length) % comps.length);
        return;
      }
      if (key.downArrow) {
        setMenuSel((s) => (s + 1) % comps.length);
        return;
      }
      const chosen = comps[Math.min(menuSel, comps.length - 1)];
      if (key.tab) {
        setEdit(fromString(applyCompletion(chosen)));
        setMenuSel(0);
        return;
      }
      if (key.return) {
        const typed = edit.value.trim();
        const isFull =
          typed.toLowerCase() === chosen.name.toLowerCase() ||
          (chosen.aliases ?? []).some((a) => a.toLowerCase() === typed.toLowerCase());
        if (!isFull && chosen.arg) {
          setEdit(fromString(applyCompletion(chosen)));
          setMenuSel(0);
        } else {
          const run = isFull ? typed : chosen.name;
          setEdit(EMPTY);
          setMenuSel(0);
          setHistory((h) => remember(h, run));
          dispatchSlash(run);
        }
        return;
      }
    }

    if (key.return) {
      submit();
      return;
    }
    if (key.leftArrow) {
      setEdit(left);
      return;
    }
    if (key.rightArrow) {
      setEdit(right);
      return;
    }
    if (key.upArrow) {
      const r = older(history, edit.value);
      setHistory(r.h);
      setEdit(fromString(r.value));
      return;
    }
    if (key.downArrow) {
      const r = newer(history);
      setHistory(r.h);
      setEdit(fromString(r.value));
      return;
    }
    if (key.ctrl && input === 'a') {
      setEdit(home);
      return;
    }
    if (key.ctrl && input === 'e') {
      setEdit(end);
      return;
    }
    if (key.ctrl && input === 'b') {
      setEdit(left);
      return;
    }
    if (key.ctrl && input === 'f') {
      setEdit(right);
      return;
    }
    if (key.ctrl && input === 'w') {
      setEdit(deleteWordBefore);
      return;
    }
    if (key.ctrl && input === 'u') {
      setEdit(killToStart);
      return;
    }
    if (key.ctrl && input === 'k') {
      setEdit(killToEnd);
      return;
    }
    if (key.backspace || key.delete) {
      setEdit(backspace);
      setHistory((hh) => (hh.pos === 0 ? hh : { ...hh, pos: 0 }));
      return;
    }
    if (input && !key.ctrl && !key.meta) {
      setEdit((e) => insert(e, input));
      setHistory((hh) => (hh.pos === 0 ? hh : { ...hh, pos: 0 }));
    }
  }, { isActive: mode === 'cockpit' });

  const comps = slashCompletions(edit.value);
  const health = !connected ? `no data from ${host}:${port} · retrying every ${POLL_MS / 1000}s` : '';

  if (mode === 'picker') {
    return (
      <SessionPicker
        sessions={sessions}
        onSelect={openSession}
        health={connected ? '' : `can’t reach dashboard at ${host}:${port} — showing known sessions`}
      />
    );
  }

  return (
    <Box flexDirection="column" paddingX={1}>
      <Header snap={snap} connected={connected} width={terminal.columns} health={health} session={session} />
      <Box marginTop={1}>
        <RolesBar roles={snap?.roles ?? []} width={terminal.columns} />
      </Box>
      {snap ? (
        <CampaignGauge campaign={snap.campaign} evolution={snap.evolution} width={terminal.columns} />
      ) : (
        <Text dimColor>{'⠿  waiting for the campaign…'}</Text>
      )}
      {panel === 'evolution' ? (
        <EvolutionPanel
          evolution={
            snap?.evolution ?? {
              tasks: [],
              skills: { new: 0, updated: 0, recent: [] },
              pending: { skill_review: 0, wiki_review: 0, plan_review: 0 },
            }
          }
          width={terminal.columns}
        />
      ) : null}
      <EventLog conversation={conversation} width={terminal.columns} />
      <LiveActivity
        roles={snap?.roles ?? []}
        steps={snap?.steps ?? []}
        reflection={snap?.reflection ?? ''}
        tick={tick}
        width={terminal.columns}
      />
      {pending ? (
        <ThinkingLine tick={tick} elapsedS={Math.max(0, Math.floor((Date.now() - startedAt) / 1000))} />
      ) : null}
      <PromptBox edit={edit} width={terminal.columns} disabled={pending} />
      <SlashMenu items={comps} selected={Math.min(menuSel, comps.length - 1)} />
      <Footer notice={notice} health={health} connected={connected} width={terminal.columns} />
    </Box>
  );
}
