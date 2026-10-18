/**
 * The loading mark: the Keystone arch assembling itself. The arch's graph nodes pop in from both
 * springs towards the crown, the edges draw between them, then the amber keystone drops into place,
 * and the arch fades to start again. CSS-only (no hooks), so it renders from a server component such
 * as a route's loading.tsx. With reduced motion the global rule stops every animation and the
 * finished arch is what shows, because each element's resting style is its final state.
 */
const CENTER = { x: 64, y: 100 };
const RADIUS = 50;
const ANGLES = [180, 148, 116, 64, 32, 0];
const CROWN = { x: 64, y: 50 };
// Pairs meet in the middle: both springs first, then their neighbours, then the two beside the crown.
const STEP = [0, 1, 2, 2, 1, 0];

const NODES = ANGLES.map(a => ({
  x: CENTER.x + RADIUS * Math.cos((a * Math.PI) / 180),
  y: CENTER.y - RADIUS * Math.sin((a * Math.PI) / 180),
}));
const CHAIN = [NODES[0], NODES[1], NODES[2], CROWN, NODES[3], NODES[4], NODES[5]];
// Edge i joins CHAIN[i] and CHAIN[i + 1]; it draws once the node it starts from has appeared.
const EDGE_STEP = [0, 1, 2, 2, 1, 0];

export function GraphLoader({ label, detail, compact = false }: { label: string; detail?: string; compact?: boolean }) {
  return <div className={compact ? 'graph-loader graph-loader-compact' : 'graph-loader'} role="status" aria-live="polite">
    <svg className="graph-loader-mark" viewBox="4 28 120 84" aria-hidden="true">
      <g className="graph-loader-edges">
        {CHAIN.slice(0, -1).map((from, i) => {
          const to = CHAIN[i + 1];
          return <line key={i} x1={from.x} y1={from.y} x2={to.x} y2={to.y} pathLength={1}
            style={{ animationDelay: `${EDGE_STEP[i] * 0.16 + 0.08}s` }} />;
        })}
      </g>
      <g className="graph-loader-nodes">
        {NODES.map((n, i) => <circle key={i} cx={n.x} cy={n.y} r={i === 0 || i === 5 ? 6.5 : 5.5}
          style={{ animationDelay: `${STEP[i] * 0.16}s` }} />)}
      </g>
      <path className="graph-loader-key" d="M52 39 H76 L70.5 61 H57.5 Z" />
    </svg>
    <p className={compact ? 'type-caption muted-text' : 'type-body-strong'}>{label}</p>
    {detail && !compact && <p className="type-caption muted-text">{detail}</p>}
  </div>;
}
