import { useMemo, useState } from 'react';
import { useCampaignStream, useManager, useSessions, useSessionTurns } from './lib/hooks';
import type { SessionRow } from './lib/types';
import { Sidebar } from './components/Sidebar';
import { TopBar } from './components/TopBar';
import { EventStream } from './components/EventStream';
import { RolesPanel } from './components/RolesPanel';
import { TaskProgressPanel } from './components/TaskProgressPanel';
import { CampaignView } from './components/CampaignView';
import { EvolutionView } from './components/EvolutionView';
import { ManagerView } from './components/ManagerView';

type View =
  | { kind: 'session'; key: string }
  | { kind: 'campaign' }
  | { kind: 'evolution' }
  | { kind: 'manager' };

function initialView(): View {
  const params = new URLSearchParams(window.location.search);
  const key = params.get('session');
  if (key) return { kind: 'session', key };
  const v = params.get('view');
  if (v === 'evolution') return { kind: 'evolution' };
  if (v === 'campaign') return { kind: 'campaign' };
  return { kind: 'manager' };
}

/** Replace the URL query so a view is deep-linkable without a full reload. */
function pushView(params: Record<string, string | null>): void {
  const url = new URL(window.location.href);
  for (const [k, v] of Object.entries(params)) {
    if (v === null) url.searchParams.delete(k);
    else url.searchParams.set(k, v);
  }
  window.history.replaceState(null, '', url.toString());
}

/** Minimal SessionRow for a "role:task" key not (yet) in the flat sessions
 *  query — keeps the transcript view mounted while useSessions catches up. The
 *  turns endpoint resolves the real thread_id/turns from the key regardless. */
function synthSession(key: string): SessionRow {
  const [role, task] = key.includes(':') ? [key.slice(0, key.indexOf(':')), key.slice(key.indexOf(':') + 1)] : ['', key];
  return {
    key,
    role,
    task,
    thread_id: '',
    last_active: null,
    task_success: false,
    success_count: 0,
    has_transcript: false,
  };
}

export default function App() {
  const managerQ = useManager();
  const sessionsQ = useSessions();
  const sessions = sessionsQ.data ?? [];
  const [view, setView] = useState<View>(initialView);
  // The campaign WS is app-global so its "● live" indicator is honest on any
  // view and there is a single stream connection app-wide.
  const { lines: campaignLines, connected } = useCampaignStream();

  const activeKey = view.kind === 'session' ? view.key : null;
  // Resolve the selected session from the flat list for its success badges; if
  // that query hasn't caught up (the sidebar tree comes from useManager, which
  // can list a key before useSessions refetches) synthesize a minimal row from
  // the "role:task" key so a selected session always renders its transcript
  // instead of silently falling back to the Manager view.
  const active: SessionRow | undefined = useMemo(
    () => (activeKey ? sessions.find((s) => s.key === activeKey) ?? synthSession(activeKey) : undefined),
    [sessions, activeKey],
  );
  const turnsQ = useSessionTurns(activeKey);
  const roleSessions = useMemo(
    () => (active ? sessions.filter((s) => s.task === active.task) : []),
    [sessions, active],
  );

  const selectSession = (key: string) => {
    setView({ kind: 'session', key });
    pushView({ session: key, view: null });
  };
  const openManager = () => {
    setView({ kind: 'manager' });
    pushView({ session: null, view: null });
  };
  const openCampaign = () => {
    setView({ kind: 'campaign' });
    pushView({ session: null, view: 'campaign' });
  };
  const openEvolution = () => {
    setView({ kind: 'evolution' });
    pushView({ session: null, view: 'evolution' });
  };

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-bg text-ink">
      <Sidebar
        groups={managerQ.data?.task_groups ?? []}
        activeKey={activeKey}
        onSelect={selectSession}
        onOpenManager={openManager}
        onOpenCampaign={openCampaign}
        onOpenEvolution={openEvolution}
        managerActive={view.kind === 'manager'}
        campaignActive={view.kind === 'campaign'}
        evolutionActive={view.kind === 'evolution'}
        loading={managerQ.isLoading}
        error={managerQ.isError ? 'error' : undefined}
      />

      <main className="flex min-w-0 flex-1 flex-col">
        {view.kind === 'session' && active ? (
          <>
            <TopBar
              session={active.thread_id ? active : { ...active, thread_id: turnsQ.data?.thread_id ?? '' }}
              turns={turnsQ.data}
              streamOk={connected}
            />
            <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 p-3 xl:grid-cols-[minmax(0,1fr)_360px]">
              <EventStream
                turns={turnsQ.data}
                loading={turnsQ.isLoading}
                connected={connected}
                sessionRole={active.role}
              />
              <div className="hidden min-h-0 flex-col gap-3 xl:flex">
                <div className="shrink-0">
                  <RolesPanel sessions={roleSessions} activeKey={activeKey} onSelect={selectSession} />
                </div>
                <TaskProgressPanel task={active.task} />
              </div>
            </div>
          </>
        ) : view.kind === 'campaign' ? (
          <CampaignView lines={campaignLines} connected={connected} />
        ) : view.kind === 'evolution' ? (
          <EvolutionView />
        ) : (
          <ManagerView onOpenSession={selectSession} onOpenEvolution={openEvolution} />
        )}
      </main>
    </div>
  );
}
