import React from 'react';
import { render } from 'ink';
import { App } from './App.js';

interface Args {
  host: string;
  port: number;
  session?: string;
  help: boolean;
}

function parseArgs(argv: string[]): Args {
  const a: Args = {
    host: process.env.ROBORSI_TUI_HOST ?? '127.0.0.1',
    port: Number(process.env.ROBORSI_TUI_PORT ?? 8791),
    session: process.env.ROBORSI_TUI_SESSION || undefined,
    help: false,
  };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    const eat = () => argv[++i];
    if (arg === '--host') a.host = eat();
    else if (arg === '--port') a.port = Number(eat());
    else if (arg === '--session') a.session = eat();
    else if (arg === '-h' || arg === '--help') a.help = true;
  }
  return a;
}

const HELP = `roborsi-tui — the terminal cockpit for the RoboRSI self-evolution Manager

Chat the Manager AND watch the live triangle (roles / campaign / evolution).
Poll-only client of the evo dashboard (scripts/evo_dashboard.py, default :8791).

Usage: roborsi-tui [--host H] [--port P] [--session NAME]

Options:
  --host H      dashboard host (default 127.0.0.1, env ROBORSI_TUI_HOST)
  --port P      dashboard port (default 8791, env ROBORSI_TUI_PORT)
  --session NAME open this Manager session directly, skipping the picker
                (env ROBORSI_TUI_SESSION)
  -h, --help    show this help and exit
`;

function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    process.stdout.write(HELP);
    return;
  }
  render(<App host={args.host} port={args.port} session={args.session} />, { exitOnCtrlC: false });
}

main();
