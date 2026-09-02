# RoboRSI Session Cockpit

A read-only web cockpit for the running RoboRSI agents. It shows every
`(role, task)` **session**, its multi-turn **Claude transcript**, per-task
**run progress** (from `~/.roborsi/trace.db`), and the live **campaign log**.

It is a pure viewer: it never writes anything and never touches the
agents/embodied code. The visual language and component layout deliberately
mirror the `argus-skill` web console (FastAPI backend + React 18 / Vite /
Tailwind / `@tanstack/react-query` + WebSocket).

```
┌──────────────┬───────────────────────────────────────────────┐
│ Sidebar      │ TopBar   task · role · solved? · thread_id     │
│  (sessions   ├───────────────────────────┬───────────────────┤
│   grouped    │ EventStream               │ RolesPanel        │
│   by task)   │  (user/assistant turns,   │ TaskProgressPanel │
│  + Campaign  │   timestamps, JSON blocks)│  (trace.db runs)  │
└──────────────┴───────────────────────────┴───────────────────┘
```

## Data sources (all read-only)

| Source | Used for |
| --- | --- |
| `~/.roborsi/agent_sessions.json` | `{"role:task": thread_id}` — the session list |
| `~/.claude/projects/<cwd-slug>/<thread_id>.jsonl` | the multi-turn Claude transcript for a session |
| `~/.roborsi/trace.db` (via `roborsi/store/trace_db.py`) | per-task runs, verified-success counts |
| `/tmp/pb/{campaign.log,current.txt,current_b.txt}` | overall campaign progress + live log |

### Claude transcript storage format

Claude Code stores each session's transcript as a JSONL file:

```
~/.claude/projects/<cwd-slug>/<session-uuid>.jsonl
```

- `<cwd-slug>` is the **repo root path** with every non-alphanumeric character
  replaced by `-`. For example, `/path/to/RoboRSI` becomes
  `-path-to-RoboRSI`.
- `<session-uuid>` is exactly the `thread_id` stored as the value in
  `agent_sessions.json`, so locating a transcript is just
  `~/.claude/projects/<slug>/<thread_id>.jsonl`.
- Each **line** is one JSON object with a `type` field. Only `type:"user"` and
  `type:"assistant"` rows are conversation turns; the rest (`mode`,
  `queue-operation`, `last-prompt`, `attachment`) are bookkeeping and skipped.
- A turn row carries `message.role` (`user`/`assistant`) and `message.content`,
  which is **either a plain string or a list of typed blocks**:
  `text`, `thinking`, `tool_use` (`{name, input}`), `tool_result` (`{content}`).
  A `user` row whose content is only `tool_result` blocks is agent plumbing (the
  tool output feeding back in), not a human/task message — those are dropped
  from the conversation view.

See `roborsi/webapi/cockpit_data.py` (`cwd_slug`, `transcript_path`,
`parse_transcript`) for the implementation.

## Backend

FastAPI + uvicorn (imported lazily; install with `pip install fastapi uvicorn`
if missing). Default bind `127.0.0.1:8795`.

```bash
# from the repo root
python scripts/session_cockpit.py --port 8795
#   --host 127.0.0.1   bind host
#   --token <t>        optional bearer auth (or env ROBORSI_WEB_TOKEN)
```

Endpoints (all under `/api`):

| Method | Path | Returns |
| --- | --- | --- |
| GET | `/api/sessions` | `[{key, role, task, thread_id, last_active, task_success, ...}]` |
| GET | `/api/sessions/{key}/turns` | `{role, task, thread_id, turns:[{role, text, ts}]}` |
| GET | `/api/tasks` | per-task run tally `[{task, total, verified_success}]` |
| GET | `/api/tasks/{task}/progress` | runs + verified successes for one task |
| GET | `/api/campaign` | `current`/`current_b` lanes + recent log lines |
| GET | `/api/events?since=<offset>` | poll fallback for new campaign lines |
| WS | `/api/stream` | live push of new `campaign.log` lines (seed + append) |

When `frontend/web/dist/` exists it is mounted at `/`, so a single port serves
both the API and the built SPA.

## Frontend

React 18 + Vite + TypeScript + Tailwind + `@tanstack/react-query`.

Node v20 lives under `~/.nvm` (`source ~/.nvm/nvm.sh` or use
`~/.nvm/versions/node/v20.20.2/bin/{node,npm}`).

```bash
cd frontend/web
npm install
npm run build          # emits frontend/web/dist/ (served by the backend)
npm run dev            # Vite dev server on :5173, proxies /api → :8795
```

For dev against a non-default backend port, set `ROBORSI_WEB_API`, e.g.
`ROBORSI_WEB_API=http://127.0.0.1:8796 npm run dev`.

### Polling / live update cadence

- sessions list: react-query poll every 5s
- selected session turns: poll every 8s
- tasks tally + task progress: poll every 6s
- campaign digest: poll every 4s
- campaign log: WebSocket push (2s server poll interval), REST `/api/events`
  fallback

## Quick start (build + run on one port)

```bash
# 1. build the SPA
cd frontend/web && npm install && npm run build && cd ../..
# 2. serve API + SPA
python scripts/session_cockpit.py --port 8795
# 3. open http://127.0.0.1:8795
```
