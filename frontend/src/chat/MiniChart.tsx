import React from "react";
import type { ChartSpec } from "../api";

// vega-embed is ~1.2 MB and only 1 of the 25 chart types needs it. Imported
// statically it rode into the entry chunk, so every page load paid for it
// whether or not a Vega chart was ever rendered.
const VegaChart = React.lazy(() => import("./VegaChart"));

/** Compact number label for chart values: 25.3 tỷ / 12.4 tr / 8.5k / 320. */
function fmtCompact(v: number): string {
  const a = Math.abs(v);
  if (a >= 1e9) return (v / 1e9).toFixed(1).replace(/\.0$/, "") + " tỷ";
  if (a >= 1e6) return (v / 1e6).toFixed(1).replace(/\.0$/, "") + " tr";
  if (a >= 1e3) return (v / 1e3).toFixed(1).replace(/\.0$/, "") + "k";
  return new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 1 }).format(v);
}

const W = 320;
const H = 180;
const PAD = 28;
const PAD_TOP = 16;
// Categorical palette — used for pie/donut/radar/heatmap where colour encodes
// the category, and as the per-series palette for multi-series charts.
// Reads from CSS variables injected by the dashboard theme, with fallbacks.
const COLORS = [
  "var(--chart-1, #4f7cff)",
  "var(--chart-2, #22b8a6)",
  "var(--chart-3, #f5a623)",
  "var(--chart-4, #e5629b)",
  "var(--chart-5, #8b6ff0)",
  "var(--chart-6, #3fb950)"
];
// A single-series line/bar shares one colour (rainbow-per-point is meaningless).
const SERIES_COLOR = "var(--chart-1, #4f7cff)";

/** Horizontal reference lines + their value labels. Without a scale the reader
 * can only compare bars to each other; with one they can read magnitudes. */
function Gridlines({ max, min = 0, height = H, pad = PAD, padTop = PAD_TOP, ticks = 3 }:
  { max: number; min?: number; height?: number; pad?: number; padTop?: number; ticks?: number }) {
  if (!isFinite(max) || max === min) return null;
  const plotH = height - pad - padTop;
  const rows = [];
  for (let t = 1; t <= ticks; t++) {
    const frac = t / ticks;
    const value = min + (max - min) * frac;
    const y = height - pad - frac * plotH;
    rows.push(
      <g key={t}>
        <line x1={pad} y1={y} x2={W - 6} y2={y} stroke="#e8ecea" strokeWidth={1} strokeDasharray="3 3" />
        <text x={pad - 4} y={y + 3} textAnchor="end" fontSize={8} fill="#9aa8a1">
          {fmtCompact(value)}
        </text>
      </g>,
    );
  }
  return <>{rows}</>;
}

function Axis({ labels }: { labels: string[] }) {
  const tickIdx = labels.length <= 1 ? [0] : [0, Math.floor((labels.length - 1) / 2), labels.length - 1];
  return (
    <div className="mini-chart__axis" style={{ display: "flex", justifyContent: "space-between", fontSize: "0.72rem", color: "#5c6b63", padding: "0.25rem 0.35rem 0" }}>
      {tickIdx.map((i) => (
        <span key={i}>{labels[i]}</span>
      ))}
    </div>
  );
}

function Legend({ labels, values, colors = COLORS }: { labels: string[]; values: number[]; colors?: string[] }) {
  return (
    <div className="mini-chart__legend">
      {labels.map((l, i) => (
        <span key={i}>
          <i style={{ background: colors[i % colors.length] }} /> {l}: {fmtCompact(values[i])}
        </span>
      ))}
    </div>
  );
}

function Frame({ title, showTitle, children }: { title: string; showTitle: boolean; children: React.ReactNode }) {
  return (
    <figure className="mini-chart">
      {showTitle && <figcaption>{title}</figcaption>}
      {children}
    </figure>
  );
}

/** Vertical bar chart. */
function BarChart({ labels, values }: { labels: string[]; values: number[] }) {
  const plotH = H - PAD - PAD_TOP;
  const max = Math.max(...values, 0);
  const bw = (W - PAD * 2) / values.length;
  const labelBars = values.length <= 14;
  return (
    <>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="bar chart">
        <Gridlines max={max} />
        <line x1={PAD} y1={H - PAD} x2={W - PAD} y2={H - PAD} stroke="#3a4a41" strokeWidth={1} />
        {values.map((v, i) => {
          const h = max ? (v / max) * plotH : 0;
          const cx = PAD + i * bw + bw * 0.5;
          return (
            <g key={i}>
              <rect x={PAD + i * bw + bw * 0.15} y={H - PAD - h} width={bw * 0.7} height={h} fill={SERIES_COLOR} rx={2} />
              {labelBars && <text x={cx} y={H - PAD - h - 4} textAnchor="middle" fontSize={9} fill="#3d4d45">{fmtCompact(v)}</text>}
            </g>
          );
        })}
      </svg>
      <Axis labels={labels} />
    </>
  );
}

/** Truncate a label to `n` chars with an ellipsis, keeping a distinguishing tail
 * (many real-world category names share a long common prefix - e.g. branch
 * names all starting "NH Cơm Ngon Si..." - so a plain head-truncate makes every
 * row look identical; keep the head AND a bit of the tail instead). */
function truncateLabel(label: string, n: number): string {
  if (label.length <= n) return label;
  const headLen = Math.ceil(n * 0.6);
  const tailLen = n - headLen - 1;
  return `${label.slice(0, headLen)}…${label.slice(label.length - tailLen)}`;
}

/** Horizontal bar chart — better for long category names / rankings. */
function HorizontalBarChart({ labels, values }: { labels: string[]; values: number[] }) {
  const rowH = Math.min(28, (H - 8) / values.length);
  const chartH = rowH * values.length + 8;
  const labelW = 118;
  const plotW = W - labelW - 44;
  const max = Math.max(...values, 0);
  return (
    <svg viewBox={`0 0 ${W} ${chartH}`} width="100%" role="img" aria-label="horizontal bar chart">
      {values.map((v, i) => {
        const w = max ? (v / max) * plotW : 0;
        const y = i * rowH + 4;
        const full = labels[i] ?? "";
        return (
          <g key={i}>
            <text x={labelW - 6} y={y + rowH * 0.55} textAnchor="end" fontSize={9} fill="#3d4d45">
              <title>{full}</title>
              {truncateLabel(full, 20)}
            </text>
            <rect x={labelW} y={y + rowH * 0.15} width={Math.max(w, 1)} height={rowH * 0.6} fill={SERIES_COLOR} rx={2} />
            <text x={labelW + w + 4} y={y + rowH * 0.55} fontSize={9} fill="#3d4d45">{fmtCompact(v)}</text>
          </g>
        );
      })}
    </svg>
  );
}

/** Lollipop chart: stem + dot, a lighter-weight alternative to bars. */
function LollipopChart({ labels, values }: { labels: string[]; values: number[] }) {
  const plotH = H - PAD - PAD_TOP;
  const max = Math.max(...values, 0);
  const bw = (W - PAD * 2) / values.length;
  return (
    <>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="lollipop chart">
        <line x1={PAD} y1={H - PAD} x2={W - PAD} y2={H - PAD} stroke="#3a4a41" strokeWidth={1} />
        {values.map((v, i) => {
          const h = max ? (v / max) * plotH : 0;
          const cx = PAD + i * bw + bw * 0.5;
          const cy = H - PAD - h;
          return (
            <g key={i}>
              <line x1={cx} y1={H - PAD} x2={cx} y2={cy} stroke={SERIES_COLOR} strokeWidth={2} />
              <circle cx={cx} cy={cy} r={4.5} fill={SERIES_COLOR} />
            </g>
          );
        })}
      </svg>
      <Axis labels={labels} />
    </>
  );
}

/** Dot plot: single dot per category positioned by value, no stem — good for a quick ranking scan. */
function DotPlotChart({ labels, values }: { labels: string[]; values: number[] }) {
  const rowH = Math.min(24, (H - 8) / values.length);
  const chartH = rowH * values.length + 8;
  const labelW = 118;
  const plotW = W - labelW - 20;
  const max = Math.max(...values, 0);
  return (
    <svg viewBox={`0 0 ${W} ${chartH}`} width="100%" role="img" aria-label="dot plot">
      {values.map((v, i) => {
        const x = labelW + (max ? (v / max) * plotW : 0);
        const y = i * rowH + rowH / 2 + 4;
        const full = labels[i] ?? "";
        return (
          <g key={i}>
            <line x1={labelW} y1={y} x2={W - 10} y2={y} stroke="#e5e9e6" strokeWidth={1} />
            <text x={labelW - 6} y={y + 3} textAnchor="end" fontSize={9} fill="#3d4d45">
              <title>{full}</title>
              {truncateLabel(full, 20)}
            </text>
            <circle cx={x} cy={y} r={5} fill={COLORS[i % COLORS.length]} />
          </g>
        );
      })}
    </svg>
  );
}

/** Line chart (also used for sparkline in minimal mode). */
function LineChart({ labels, values, sparkline = false }: { labels: string[]; values: number[]; sparkline?: boolean }) {
  const h = sparkline ? 48 : H;
  const pad = sparkline ? 4 : PAD;
  const padTop = sparkline ? 4 : PAD_TOP;
  const max = Math.max(...values, 0);
  const min = Math.min(...values, 0);
  const range = max - min || 1;
  const plotH = h - pad - padTop;
  const stepX = (W - pad * 2) / Math.max(labels.length - 1, 1);
  const yOf = (v: number) => h - pad - ((v - min) / range) * plotH;
  const pts = values.map((v, i) => `${pad + i * stepX},${yOf(v)}`).join(" ");
  const labelPts = !sparkline && values.length <= 8;
  return (
    <>
      <svg viewBox={`0 0 ${W} ${h}`} width="100%" role="img" aria-label="line chart">
        {!sparkline && <Gridlines max={max} min={min} height={h} pad={pad} padTop={padTop} />}
        {!sparkline && <line x1={pad} y1={h - pad} x2={W - pad} y2={h - pad} stroke="#3a4a41" strokeWidth={1} />}
        <polyline points={pts} fill="none" stroke={SERIES_COLOR} strokeWidth={2} />
        {values.map((v, i) => (
          <g key={i}>
            <circle cx={pad + i * stepX} cy={yOf(v)} r={sparkline ? 2 : 3} fill={SERIES_COLOR} />
            {labelPts && <text x={pad + i * stepX} y={yOf(v) - 6} textAnchor="middle" fontSize={9} fill="#3d4d45">{fmtCompact(v)}</text>}
          </g>
        ))}
      </svg>
      {!sparkline && <Axis labels={labels} />}
    </>
  );
}

/** Area chart: filled line, emphasizes cumulative volume over a trend. */
function AreaChart({ labels, values }: { labels: string[]; values: number[] }) {
  const max = Math.max(...values, 0);
  const plotH = H - PAD - PAD_TOP;
  const stepX = (W - PAD * 2) / Math.max(labels.length - 1, 1);
  const yOf = (v: number) => H - PAD - (max ? (v / max) * plotH : 0);
  const linePts = values.map((v, i) => `${PAD + i * stepX},${yOf(v)}`).join(" ");
  const areaPts = `${PAD},${H - PAD} ${linePts} ${PAD + (values.length - 1) * stepX},${H - PAD}`;
  return (
    <>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="area chart">
        <Gridlines max={max} />
        <line x1={PAD} y1={H - PAD} x2={W - PAD} y2={H - PAD} stroke="#3a4a41" strokeWidth={1} />
        <polygon points={areaPts} fill={SERIES_COLOR} fillOpacity={0.22} stroke="none" />
        <polyline points={linePts} fill="none" stroke={SERIES_COLOR} strokeWidth={2} />
      </svg>
      <Axis labels={labels} />
    </>
  );
}

function pieSlices(values: number[], cx: number, cy: number, r: number, innerR = 0) {
  const total = values.reduce((a, b) => a + b, 0) || 1;
  let angle = -Math.PI / 2;
  return values.map((v, i) => {
    const slice = (v / total) * 2 * Math.PI;
    const x1 = cx + r * Math.cos(angle);
    const y1 = cy + r * Math.sin(angle);
    const ix1 = cx + innerR * Math.cos(angle);
    const iy1 = cy + innerR * Math.sin(angle);
    angle += slice;
    const x2 = cx + r * Math.cos(angle);
    const y2 = cy + r * Math.sin(angle);
    const ix2 = cx + innerR * Math.cos(angle);
    const iy2 = cy + innerR * Math.sin(angle);
    const large = slice > Math.PI ? 1 : 0;
    const d = innerR
      ? `M${ix1},${iy1} L${x1},${y1} A${r},${r} 0 ${large},1 ${x2},${y2} L${ix2},${iy2} A${innerR},${innerR} 0 ${large},0 ${ix1},${iy1} Z`
      : `M${cx},${cy} L${x1},${y1} A${r},${r} 0 ${large},1 ${x2},${y2} Z`;
    return <path key={i} d={d} fill={COLORS[i % COLORS.length]} />;
  });
}

/** Pie / donut chart. */
function PieChart({ labels, values, donut = false }: { labels: string[]; values: number[]; donut?: boolean }) {
  const cx = W / 2;
  const cy = H / 2;
  const r = 70;
  const total = values.reduce((a, b) => a + b, 0);
  return (
    <>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={donut ? "donut chart" : "pie chart"}>
        {pieSlices(values, cx, cy, r, donut ? 38 : 0)}
        {donut && (
          <text x={cx} y={cy + 4} textAnchor="middle" fontSize={13} fontWeight={600} fill="#243530">
            {fmtCompact(total)}
          </text>
        )}
      </svg>
      <Legend labels={labels} values={values} />
    </>
  );
}

/** Funnel (top-down narrowing) / pyramid (bottom-up widening) — same geometry, reversed order. */
function FunnelChart({ labels, values, pyramid = false }: { labels: string[]; values: number[]; pyramid?: boolean }) {
  const rows = pyramid ? [...values].reverse() : values;
  const rowLabels = pyramid ? [...labels].reverse() : labels;
  const max = Math.max(...rows, 1);
  const rowH = (H - 8) / rows.length;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={pyramid ? "pyramid chart" : "funnel chart"}>
      {rows.map((v, i) => {
        const w = Math.max((v / max) * (W - 60), 20);
        const x = (W - w) / 2;
        const y = i * rowH + 4;
        return (
          <g key={i}>
            <rect x={x} y={y} width={w} height={rowH - 6} fill={COLORS[i % COLORS.length]} rx={3} />
            <text x={W / 2} y={y + (rowH - 6) / 2 + 3} textAnchor="middle" fontSize={9} fill="#fff">
              {(rowLabels[i] ?? "").slice(0, 16)} · {fmtCompact(v)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

/** Waterfall: each value is a signed delta from the running total (first bar = starting total). */
function WaterfallChart({ labels, values }: { labels: string[]; values: number[] }) {
  let running = 0;
  const bars = values.map((v, i) => {
    const start = i === 0 ? 0 : running;
    running += v;
    return { start, end: running, v, isTotal: i === 0 };
  });
  const allVals = bars.flatMap((b) => [b.start, b.end]);
  const max = Math.max(...allVals, 0);
  const min = Math.min(...allVals, 0);
  const range = max - min || 1;
  const plotH = H - PAD - PAD_TOP;
  const bw = (W - PAD * 2) / values.length;
  const yOf = (v: number) => H - PAD - ((v - min) / range) * plotH;
  return (
    <>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="waterfall chart">
        <line x1={PAD} y1={H - PAD} x2={W - PAD} y2={H - PAD} stroke="#3a4a41" strokeWidth={1} />
        {bars.map((b, i) => {
          const top = yOf(Math.max(b.start, b.end));
          const bottom = yOf(Math.min(b.start, b.end));
          const color = b.isTotal ? "#8b6ff0" : b.v >= 0 ? "#3fb950" : "#e5629b";
          const cx = PAD + i * bw + bw * 0.5;
          return (
            <g key={i}>
              <rect x={PAD + i * bw + bw * 0.15} y={top} width={bw * 0.7} height={Math.max(bottom - top, 1)} fill={color} rx={2} />
              <text x={cx} y={top - 4} textAnchor="middle" fontSize={9} fill="#3d4d45">{fmtCompact(b.v)}</text>
            </g>
          );
        })}
      </svg>
      <Axis labels={labels} />
    </>
  );
}

/** Gauge: half-circle showing value vs. max, with an optional target tick. */
function GaugeChart({ value, target, max, title }: { value: number; target?: number; max?: number; title: string }) {
  const m = max ?? Math.max(value, target ?? 0, 1);
  const cx = W / 2;
  const cy = H - 30;
  const r = 80;
  const pct = Math.max(0, Math.min(1, value / m));
  const angleFor = (p: number) => Math.PI - p * Math.PI;
  const arcPoint = (p: number, rad: number) => {
    const a = angleFor(p);
    return [cx + rad * Math.cos(a), cy - rad * Math.sin(a)];
  };
  const [x1, y1] = arcPoint(0, r);
  const [x2, y2] = arcPoint(1, r);
  const [vx, vy] = arcPoint(pct, r);
  const large = pct > 0.5 ? 1 : 0;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="gauge">
      <path d={`M${x1},${y1} A${r},${r} 0 1,1 ${x2},${y2}`} fill="none" stroke="#e5e9e6" strokeWidth={14} />
      <path d={`M${x1},${y1} A${r},${r} 0 ${large},1 ${vx},${vy}`} fill="none" stroke={SERIES_COLOR} strokeWidth={14} strokeLinecap="round" />
      {target != null && (() => {
        const [tx1, ty1] = arcPoint(Math.min(target / m, 1), r - 10);
        const [tx2, ty2] = arcPoint(Math.min(target / m, 1), r + 10);
        return <line x1={tx1} y1={ty1} x2={tx2} y2={ty2} stroke="#e5629b" strokeWidth={3} />;
      })()}
      <text x={cx} y={cy - 10} textAnchor="middle" fontSize={20} fontWeight={700} fill="#243530">{fmtCompact(value)}</text>
      <text x={cx} y={cy + 10} textAnchor="middle" fontSize={10} fill="#5c6b63">{title}</text>
    </svg>
  );
}

/** Bullet chart: compact horizontal bar with a qualitative range band + target marker. */
function BulletChart({ value, target, max, title }: { value: number; target?: number; max?: number; title: string }) {
  const m = max ?? Math.max(value, target ?? 0, 1) * 1.2;
  const plotW = W - 20;
  const y = 40;
  const w = (value / m) * plotW;
  return (
    <svg viewBox={`0 0 ${W} 80`} width="100%" role="img" aria-label="bullet chart">
      <text x={10} y={16} fontSize={10} fill="#5c6b63">{title}</text>
      <rect x={10} y={y - 10} width={plotW} height={20} fill="#e5e9e6" rx={3} />
      <rect x={10} y={y - 6} width={Math.max(w, 1)} height={12} fill={SERIES_COLOR} rx={2} />
      {target != null && <line x1={10 + (target / m) * plotW} y1={y - 14} x2={10 + (target / m) * plotW} y2={y + 14} stroke="#e5629b" strokeWidth={3} />}
      <text x={10} y={y + 28} fontSize={11} fontWeight={600} fill="#243530">{fmtCompact(value)}{target != null ? ` / mục tiêu ${fmtCompact(target)}` : ""}</text>
    </svg>
  );
}

/** Progress ring: single value as a percentage of max, drawn as a full-circle ring. */
function ProgressChart({ value, max, title }: { value: number; max?: number; title: string }) {
  const m = max ?? 100;
  const pct = Math.max(0, Math.min(1, value / m));
  const cx = W / 2;
  const cy = H / 2;
  const r = 60;
  const circumference = 2 * Math.PI * r;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="progress ring">
      <circle cx={cx} cy={cy} r={r} fill="none" stroke="#e5e9e6" strokeWidth={16} />
      <circle
        cx={cx}
        cy={cy}
        r={r}
        fill="none"
        stroke={SERIES_COLOR}
        strokeWidth={16}
        strokeDasharray={`${circumference * pct} ${circumference}`}
        strokeLinecap="round"
        transform={`rotate(-90 ${cx} ${cy})`}
      />
      <text x={cx} y={cy - 4} textAnchor="middle" fontSize={20} fontWeight={700} fill="#243530">{Math.round(pct * 100)}%</text>
      <text x={cx} y={cy + 16} textAnchor="middle" fontSize={10} fill="#5c6b63">{title}</text>
    </svg>
  );
}

/** Radial bar: like a bar chart but bars are concentric arcs — compact multi-category comparison. */
function RadialBarChart({ labels, values }: { labels: string[]; values: number[] }) {
  const max = Math.max(...values, 1);
  const cx = W / 2;
  const cy = H / 2;
  const rOuter = 78;
  const thickness = Math.min(14, (rOuter - 10) / values.length - 2);
  return (
    <>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="radial bar chart">
        {values.map((v, i) => {
          const r = rOuter - i * (thickness + 3);
          const pct = v / max;
          const circumference = 2 * Math.PI * r;
          return (
            <g key={i}>
              <circle cx={cx} cy={cy} r={r} fill="none" stroke="#e5e9e6" strokeWidth={thickness} />
              <circle
                cx={cx}
                cy={cy}
                r={r}
                fill="none"
                stroke={COLORS[i % COLORS.length]}
                strokeWidth={thickness}
                strokeDasharray={`${circumference * pct} ${circumference}`}
                strokeLinecap="round"
                transform={`rotate(-90 ${cx} ${cy})`}
              />
            </g>
          );
        })}
      </svg>
      <Legend labels={labels} values={values} />
    </>
  );
}

/** Multi-series bars: stacked or grouped side-by-side. */
function MultiBarChart({ labels, series, stacked }: { labels: string[]; series: { name: string; values: number[] }[]; stacked: boolean }) {
  const plotH = H - PAD - PAD_TOP;
  const groupW = (W - PAD * 2) / labels.length;
  const totals = labels.map((_, i) => series.reduce((s, ser) => s + (ser.values[i] ?? 0), 0));
  const maxStacked = Math.max(...totals, 0);
  const maxGrouped = Math.max(...series.flatMap((s) => s.values), 0);
  const max = stacked ? maxStacked : maxGrouped;
  const barW = stacked ? groupW * 0.6 : (groupW * 0.8) / series.length;
  return (
    <>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={stacked ? "stacked bar chart" : "grouped bar chart"}>
        <Gridlines max={max} />
        <line x1={PAD} y1={H - PAD} x2={W - PAD} y2={H - PAD} stroke="#3a4a41" strokeWidth={1} />
        {labels.map((_, i) => {
          let stackY = H - PAD;
          const groupX = PAD + i * groupW + groupW * 0.1;
          return series.map((s, si) => {
            const v = s.values[i] ?? 0;
            const h = max ? (v / max) * plotH : 0;
            if (stacked) {
              const y = stackY - h;
              stackY = y;
              return <rect key={si} x={groupX} y={y} width={barW} height={h} fill={COLORS[si % COLORS.length]} />;
            }
            const x = groupX + si * barW;
            return <rect key={si} x={x} y={H - PAD - h} width={barW * 0.9} height={h} fill={COLORS[si % COLORS.length]} rx={1} />;
          });
        })}
      </svg>
      <Axis labels={labels} />
      <div className="mini-chart__legend">
        {series.map((s, i) => (
          <span key={i}><i style={{ background: COLORS[i % COLORS.length] }} /> {s.name}</span>
        ))}
      </div>
    </>
  );
}

/** Multi-line / stacked-area / combo (first series bar, rest lines) share one axis-based layout. */
function MultiSeriesLineChart({ labels, series, mode }: { labels: string[]; series: { name: string; values: number[] }[]; mode: "multi-line" | "stacked-area" | "combo" }) {
  const plotH = H - PAD - PAD_TOP;
  const stepX = (W - PAD * 2) / Math.max(labels.length - 1, 1);
  const totals = labels.map((_, i) => series.reduce((s, ser) => s + (ser.values[i] ?? 0), 0));
  const maxAll = mode === "stacked-area" ? Math.max(...totals, 0) : Math.max(...series.flatMap((s) => s.values), 0);
  const yOf = (v: number) => H - PAD - (maxAll ? (v / maxAll) * plotH : 0);

  // Stacked-area needs each series' RUNNING total (band N = sum of series 0..N),
  // drawn back-to-front (largest/last band first) so the smaller bands drawn
  // afterwards sit visibly on top instead of being buried under a bigger fill.
  let acc = new Array(labels.length).fill(0);
  const cumulative = series.map((s) => s.values.map((v, i) => (acc[i] += v)));
  const drawOrder = mode === "stacked-area" ? [...series.keys()].reverse() : series.keys();

  return (
    <>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={mode}>
        <Gridlines max={maxAll} />
        <line x1={PAD} y1={H - PAD} x2={W - PAD} y2={H - PAD} stroke="#3a4a41" strokeWidth={1} />
        {mode === "combo" && series[0] && (() => {
          const bw = (W - PAD * 2) / labels.length;
          const max0 = Math.max(...series[0].values, 0);
          return series[0].values.map((v, i) => {
            const h = max0 ? (v / max0) * plotH : 0;
            return <rect key={i} x={PAD + i * bw + bw * 0.2} y={H - PAD - h} width={bw * 0.6} height={h} fill={COLORS[0]} rx={2} opacity={0.6} />;
          });
        })()}
        {[...drawOrder].map((si) => {
          if (mode === "combo" && si === 0) return null;
          const values = mode === "stacked-area" ? cumulative[si] : series[si].values;
          const pts = values.map((v, i) => `${PAD + i * stepX},${yOf(v)}`).join(" ");
          if (mode === "stacked-area") {
            const areaPts = `${PAD},${H - PAD} ${pts} ${PAD + (values.length - 1) * stepX},${H - PAD}`;
            return <polygon key={si} points={areaPts} fill={COLORS[si % COLORS.length]} fillOpacity={0.85} stroke={COLORS[si % COLORS.length]} strokeWidth={1.5} />;
          }
          return <polyline key={si} points={pts} fill="none" stroke={COLORS[si % COLORS.length]} strokeWidth={2} />;
        })}
      </svg>
      <Axis labels={labels} />
      <div className="mini-chart__legend">
        {series.map((s, i) => (
          <span key={i}><i style={{ background: COLORS[i % COLORS.length] }} /> {s.name}</span>
        ))}
      </div>
    </>
  );
}

/** Radar / spider chart: labels are axes, each series is a polygon. */
function RadarChart({ labels, series }: { labels: string[]; series: { name: string; values: number[] }[] }) {
  const cx = W / 2;
  const cy = H / 2;
  const r = 70;
  const n = labels.length || 1;
  const max = Math.max(...series.flatMap((s) => s.values), 1);
  const angleFor = (i: number) => -Math.PI / 2 + (i / n) * 2 * Math.PI;
  const axisPoint = (i: number, rad: number) => [cx + rad * Math.cos(angleFor(i)), cy + rad * Math.sin(angleFor(i))];
  return (
    <>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="radar chart">
        {[0.33, 0.66, 1].map((f, gi) => (
          <polygon
            key={gi}
            points={labels.map((_, i) => axisPoint(i, r * f).join(",")).join(" ")}
            fill="none"
            stroke="#e5e9e6"
            strokeWidth={1}
          />
        ))}
        {labels.map((_, i) => {
          const [x, y] = axisPoint(i, r);
          return <line key={i} x1={cx} y1={cy} x2={x} y2={y} stroke="#e5e9e6" strokeWidth={1} />;
        })}
        {series.map((s, si) => (
          <polygon
            key={si}
            points={s.values.map((v, i) => axisPoint(i, (v / max) * r).join(",")).join(" ")}
            fill={COLORS[si % COLORS.length]}
            fillOpacity={0.25}
            stroke={COLORS[si % COLORS.length]}
            strokeWidth={2}
          />
        ))}
        {labels.map((l, i) => {
          const [x, y] = axisPoint(i, r + 12);
          return <text key={i} x={x} y={y} textAnchor="middle" fontSize={9} fill="#5c6b63">{l.slice(0, 10)}</text>;
        })}
      </svg>
      {series.length > 1 && (
        <div className="mini-chart__legend">
          {series.map((s, i) => (
            <span key={i}><i style={{ background: COLORS[i % COLORS.length] }} /> {s.name}</span>
          ))}
        </div>
      )}
    </>
  );
}

/** Scatter / bubble: raw (x, y[, size]) points — the one case row-level data is appropriate. */
function ScatterChart({ points, bubble }: { points: { x: number; y: number; label?: string; size?: number }[]; bubble: boolean }) {
  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const minX = Math.min(...xs, 0);
  const maxX = Math.max(...xs, 1);
  const minY = Math.min(...ys, 0);
  const maxY = Math.max(...ys, 1);
  const plotW = W - PAD * 2;
  const plotH = H - PAD - PAD_TOP;
  const xOf = (v: number) => PAD + ((v - minX) / (maxX - minX || 1)) * plotW;
  const yOf = (v: number) => H - PAD - ((v - minY) / (maxY - minY || 1)) * plotH;
  const sizes = points.map((p) => p.size ?? 1);
  const maxSize = Math.max(...sizes, 1);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={bubble ? "bubble chart" : "scatter chart"}>
      <line x1={PAD} y1={H - PAD} x2={W - PAD} y2={H - PAD} stroke="#3a4a41" strokeWidth={1} />
      <line x1={PAD} y1={PAD_TOP} x2={PAD} y2={H - PAD} stroke="#3a4a41" strokeWidth={1} />
      {points.map((p, i) => {
        const r = bubble ? 3 + ((p.size ?? 1) / maxSize) * 12 : 4;
        return <circle key={i} cx={xOf(p.x)} cy={yOf(p.y)} r={r} fill={COLORS[i % COLORS.length]} fillOpacity={0.75} />;
      })}
    </svg>
  );
}

/** Heatmap: matrix[row][col] rendered as a colour-intensity grid. */
function HeatmapChart({ labels, rowLabels, matrix }: { labels: string[]; rowLabels: string[]; matrix: number[][] }) {
  const rows = matrix.length;
  const cols = labels.length;
  const labelW = 70;
  const cellW = (W - labelW - 10) / Math.max(cols, 1);
  const cellH = Math.min(22, (H - 24) / Math.max(rows, 1));
  const flat = matrix.flat();
  const max = Math.max(...flat, 1);
  const min = Math.min(...flat, 0);
  const colorFor = (v: number) => {
    const t = (v - min) / (max - min || 1);
    const g = Math.round(190 - t * 140);
    return `rgb(${Math.round(79 + t * 20)}, ${g}, ${Math.round(120 - t * 40)})`;
  };
  return (
    <svg viewBox={`0 0 ${W} ${rows * cellH + 24}`} width="100%" role="img" aria-label="heatmap">
      {labels.map((l, ci) => (
        <text key={ci} x={labelW + ci * cellW + cellW / 2} y={12} textAnchor="middle" fontSize={8} fill="#5c6b63">{l.slice(0, 6)}</text>
      ))}
      {matrix.map((row, ri) => (
        <g key={ri}>
          <text x={labelW - 6} y={20 + ri * cellH + cellH / 2} textAnchor="end" fontSize={9} fill="#5c6b63">{(rowLabels[ri] ?? "").slice(0, 10)}</text>
          {row.map((v, ci) => (
            <rect key={ci} x={labelW + ci * cellW} y={18 + ri * cellH} width={cellW - 2} height={cellH - 2} fill={colorFor(v)} rx={2} />
          ))}
        </g>
      ))}
    </svg>
  );
}

/** Dependency-free SVG chart library for chat answers and dashboard cards.
 * Vega is the escape hatch for anything genuinely outside this set (e.g. real
 * geo maps) - everything a typical business dashboard needs is a hand-drawn
 * type here so colours always follow the active palette and render is instant.
 * `showTitle=false` when the surrounding card already renders the title
 * (dashboard cards), so it isn't drawn twice. */
/** Say so when nothing can be drawn.
 *
 *  Every branch below bails with `return null` when the spec's data array for
 *  its type is missing — eleven separate exits, all silent. Callers decide
 *  whether to SHOW a chart by testing that the spec object exists, so a spec
 *  with a type but no data opened an empty popup with a "Ghi vào Dashboard"
 *  button floating in it. The backend no longer emits those, but a renderer
 *  that fails invisibly will hide the next such bug just as well, so it now
 *  reports instead. */
function EmptyChart({ title, showTitle }: { title?: string; showTitle: boolean }) {
  return (
    <figure className="mini-chart mini-chart--empty">
      {showTitle && title && <figcaption>{title}</figcaption>}
      <div className="mini-chart__empty-body">
        <span aria-hidden="true">📉</span>
        <p>Không đủ dữ liệu để vẽ biểu đồ này.</p>
      </div>
    </figure>
  );
}

export default function MiniChart(props: { spec: ChartSpec; showTitle?: boolean }) {
  if (!props.spec) {
    return <EmptyChart title="" showTitle={props.showTitle ?? true} />;
  }
  try {
    const drawn = renderChart(props);
    if (drawn) return drawn;
    return <EmptyChart title={props.spec?.title} showTitle={props.showTitle ?? true} />;
  } catch (err) {
    console.error("Failed to render MiniChart:", err);
    return <EmptyChart title={props.spec?.title || "Lỗi biểu đồ"} showTitle={props.showTitle ?? true} />;
  }
}

function renderChart({ spec, showTitle = true }: { spec: ChartSpec; showTitle?: boolean }) {
  if (!spec) return null;
  const rawLabels = Array.isArray(spec.labels) ? spec.labels : [];
  const rawValues = Array.isArray(spec.values) ? spec.values : [];
  const labels = rawLabels.map((l) => String(l ?? ""));
  const values = rawValues.map((v) => (typeof v === "number" && !isNaN(v) ? v : Number(v) || 0));
  const { type, title = "", series, points, matrix, rowLabels, target, max } = spec;

  if (type === "vega" && spec.vegaLiteSpec) {
    return (
      <figure className="mini-chart" style={{ padding: "0.5rem" }}>
        {showTitle && <figcaption>{title}</figcaption>}
        <React.Suspense fallback={<div className="mini-chart__loading">Đang nạp biểu đồ…</div>}>
          <VegaChart spec={spec.vegaLiteSpec} />
        </React.Suspense>
      </figure>
    );
  }

  if (type === "scatter" || type === "bubble") {
    if (!points?.length) return null;
    return (
      <Frame title={title} showTitle={showTitle}>
        <ScatterChart points={points} bubble={type === "bubble"} />
      </Frame>
    );
  }

  if (type === "heatmap") {
    if (!matrix?.length) return null;
    return (
      <Frame title={title} showTitle={showTitle}>
        <HeatmapChart labels={labels} rowLabels={rowLabels ?? []} matrix={matrix} />
      </Frame>
    );
  }

  if (type === "stacked-bar" || type === "grouped-bar") {
    if (!series?.length) return null;
    return (
      <Frame title={title} showTitle={showTitle}>
        <MultiBarChart labels={labels} series={series} stacked={type === "stacked-bar"} />
      </Frame>
    );
  }

  if (type === "multi-line" || type === "stacked-area" || type === "combo") {
    if (!series?.length) return null;
    return (
      <Frame title={title} showTitle={showTitle}>
        <MultiSeriesLineChart labels={labels} series={series} mode={type} />
      </Frame>
    );
  }

  if (type === "radar") {
    if (!series?.length) return null;
    return (
      <Frame title={title} showTitle={showTitle}>
        <RadarChart labels={labels} series={series} />
      </Frame>
    );
  }

  if (type === "gauge") {
    if (!values.length) return null;
    return (
      <Frame title={title} showTitle={false}>
        <GaugeChart value={values[0]} target={target} max={max} title={title} />
      </Frame>
    );
  }

  if (type === "bullet") {
    if (!values.length) return null;
    return (
      <Frame title={title} showTitle={false}>
        <BulletChart value={values[0]} target={target} max={max} title={title} />
      </Frame>
    );
  }

  if (type === "progress") {
    if (!values.length) return null;
    return (
      <Frame title={title} showTitle={false}>
        <ProgressChart value={values[0]} max={max} title={title} />
      </Frame>
    );
  }

  if (!values.length) return null;

  switch (type) {
    case "pie":
      return <Frame title={title} showTitle={showTitle}><PieChart labels={labels} values={values} /></Frame>;
    case "donut":
      return <Frame title={title} showTitle={showTitle}><PieChart labels={labels} values={values} donut /></Frame>;
    case "horizontal-bar":
      return <Frame title={title} showTitle={showTitle}><HorizontalBarChart labels={labels} values={values} /></Frame>;
    case "lollipop":
      return <Frame title={title} showTitle={showTitle}><LollipopChart labels={labels} values={values} /></Frame>;
    case "dot-plot":
      return <Frame title={title} showTitle={showTitle}><DotPlotChart labels={labels} values={values} /></Frame>;
    case "line":
      return <Frame title={title} showTitle={showTitle}><LineChart labels={labels} values={values} /></Frame>;
    case "sparkline":
      return <Frame title={title} showTitle={showTitle}><LineChart labels={labels} values={values} sparkline /></Frame>;
    case "area":
      return <Frame title={title} showTitle={showTitle}><AreaChart labels={labels} values={values} /></Frame>;
    case "funnel":
      return <Frame title={title} showTitle={showTitle}><FunnelChart labels={labels} values={values} /></Frame>;
    case "pyramid":
      return <Frame title={title} showTitle={showTitle}><FunnelChart labels={labels} values={values} pyramid /></Frame>;
    case "waterfall":
      return <Frame title={title} showTitle={showTitle}><WaterfallChart labels={labels} values={values} /></Frame>;
    case "radial-bar":
      return <Frame title={title} showTitle={showTitle}><RadialBarChart labels={labels} values={values} /></Frame>;
    case "bar":
    default:
      return <Frame title={title} showTitle={showTitle}><BarChart labels={labels} values={values} /></Frame>;
  }
}
