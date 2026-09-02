import type { ReactNode } from 'react';
import { markerColor } from '../lib/format';

/**
 * Lightweight renderer for assistant text: splits fenced code blocks (```lang …
 * ```) from prose, pretty-prints JSON blocks, and keeps prose as pre-wrapped
 * mono text. Deliberately minimal — no full markdown engine — so agent
 * transcripts (mostly JSON plans + tool logs) stay legible without a dependency.
 */

interface Segment {
  kind: 'prose' | 'code';
  lang: string;
  text: string;
}

const FENCE = /```([a-zA-Z0-9_-]*)\n([\s\S]*?)```/g;

function splitFences(text: string): Segment[] {
  const out: Segment[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  FENCE.lastIndex = 0;
  while ((m = FENCE.exec(text)) !== null) {
    if (m.index > last) out.push({ kind: 'prose', lang: '', text: text.slice(last, m.index) });
    out.push({ kind: 'code', lang: m[1] || '', text: m[2] });
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push({ kind: 'prose', lang: '', text: text.slice(last) });
  return out;
}

function prettyJson(raw: string): string {
  const trimmed = raw.trim();
  try {
    return JSON.stringify(JSON.parse(trimmed), null, 2);
  } catch {
    return raw;
  }
}

function CodeBlock({ lang, text }: { lang: string; text: string }) {
  const body = lang === 'json' || lang === '' ? prettyJson(text) : text;
  return (
    <pre className="my-1.5 overflow-x-auto rounded border border-line bg-bg/60 px-3 py-2 font-mono text-[11px] leading-relaxed text-blue-sky scroll-thin">
      {body}
    </pre>
  );
}

/** A bracketed inline marker like "[tool: Bash] {...}" or "[thinking]" gets a
 *  muted hue from the shared palette; everything else is plain prose. */
export function renderMessage(text: string): ReactNode {
  return splitFences(text).map((seg, i) => {
    if (seg.kind === 'code') return <CodeBlock key={i} lang={seg.lang} text={seg.text} />;
    const color = markerColor(seg.text);
    return (
      <p key={i} className="my-1 whitespace-pre-wrap break-words leading-relaxed" style={color ? { color } : undefined}>
        {seg.text}
      </p>
    );
  });
}
