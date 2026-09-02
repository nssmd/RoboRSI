import type { ReactNode } from 'react';
import { bucketHue } from '../lib/format';

/** A steady status dot. Motion is reserved for real loading operations. */
export function StatusDot({ ok, title }: { ok: boolean; title?: string }) {
  return (
    <span
      title={title}
      className={`inline-block h-1.5 w-1.5 rounded-full ${ok ? 'bg-ok' : 'bg-ink-faint'}`}
    />
  );
}

export function Chip({
  children,
  color,
  className = '',
}: {
  children: ReactNode;
  color?: string;
  className?: string;
}) {
  return (
    <span
      className={`chip text-ink-dim ${className}`}
      style={color ? { color, borderColor: `${color}44` } : undefined}
    >
      {children}
    </span>
  );
}

/** A section header used across the panels. */
export function PanelHeader({ title, right }: { title: string; right?: ReactNode }) {
  return (
    <div className="flex items-center justify-between border-b border-line px-3 py-2">
      <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-faint">{title}</span>
      {right}
    </div>
  );
}

export function EmptyHint({ children }: { children: ReactNode }) {
  return <div className="px-3 py-6 text-center text-xs text-ink-faint">{children}</div>;
}

/** Product mark: one neutral registration block + a word (no gradient/ornament). */
export function Wordmark({ size = 20, tag }: { size?: number; tag?: string }) {
  return (
    <span className="inline-flex select-none items-center gap-2.5">
      <span className="inline-flex items-center gap-2" style={{ fontSize: size, fontWeight: 650, letterSpacing: '-0.025em' }}>
        <span aria-hidden="true" className="inline-block h-[0.62em] w-[0.62em] border border-ink-faint bg-ink-dim" />
        <span className="text-ink">roborsi</span>
      </span>
      {tag && <span className="text-[10px] font-medium uppercase tracking-[0.16em] text-ink-faint">{tag}</span>}
    </span>
  );
}

/** A KPI pill: big number over a small label, with an optional accent colour. */
export function Stat({
  label,
  value,
  accent,
  hint,
}: {
  label: string;
  value: ReactNode;
  accent?: string;
  hint?: string;
}) {
  return (
    <div className="stat animate-fade-in" title={hint}>
      <span className="stat-num" style={accent ? { color: accent } : undefined}>
        {value}
      </span>
      <span className="stat-label">{label}</span>
    </div>
  );
}

/** A stacked horizontal funnel bar: pending / approved / rejected (+ other). */
export function FunnelBar({
  pending,
  approved,
  rejected,
  other = 0,
}: {
  pending: number;
  approved: number;
  rejected: number;
  other?: number;
}) {
  const total = pending + approved + rejected + other;
  const seg = (n: number, bucket: string) =>
    total > 0 && n > 0 ? (
      <span
        style={{ width: `${(n / total) * 100}%`, background: bucketHue(bucket) }}
        title={`${bucket}: ${n}`}
      />
    ) : null;
  return (
    <div className="funnel" title={`${total} proposals`}>
      {total === 0 ? <span style={{ width: '100%', background: '#282a24' }} /> : null}
      {seg(pending, 'pending')}
      {seg(approved, 'approved')}
      {seg(rejected, 'rejected')}
      {seg(other, 'other')}
    </div>
  );
}
