#!/usr/bin/env node
// `roborsi-tui` launcher — the terminal cockpit (Ink) for the RoboRSI
// self-evolution Manager. It prefers the compiled build (dist/cli.js) when it is
// up-to-date; if any source file is newer than the build (or there is no build
// yet) it runs the TS source directly through tsx, so editing src/ and
// relaunching always runs the latest code (no stale-dist trap).
import { existsSync, statSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');
const dist = join(root, 'dist', 'cli.js');
const src = join(root, 'src', 'cli.tsx');

/** Newest mtime (ms) of any file under a directory tree, or 0 if absent. */
function newestMtime(dir) {
  let newest = 0;
  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch {
    return 0;
  }
  for (const e of entries) {
    const p = join(dir, e.name);
    if (e.isDirectory()) newest = Math.max(newest, newestMtime(p));
    else {
      try {
        newest = Math.max(newest, statSync(p).mtimeMs);
      } catch {
        /* ignore a vanishing file */
      }
    }
  }
  return newest;
}

const distFresh =
  existsSync(dist) && newestMtime(join(root, 'src')) <= statSync(dist).mtimeMs;

if (distFresh) {
  await import(dist);
} else {
  // No build, or the source is newer than the build → run the current source
  // through tsx so the launcher never runs stale code.
  const { register } = await import('tsx/esm/api');
  register();
  await import(src);
}
