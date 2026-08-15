/** Skeleton loading state while a file uploads and the AI comprehension pass
 * runs. Kept intentionally calm: a small ghost grid (8×6) with a single
 * shimmer sweep across the whole surface + rows that fade out toward the
 * bottom, giving the impression data is "pouring in" without overwhelming
 * the eye. */

const ROWS = 8;
const COLS = 6;

/** Only 3 bar widths — just enough variation to look organic. */
const WIDTHS = ["40%", "62%", "80%"];
const widthFor = (i: number) => WIDTHS[i % WIDTHS.length];

/** `head` renders above the grid — the WorkBench in normal use. Kept as a slot
 *  rather than imported directly so the ghost grid stays reusable for any
 *  waiting screen, including ones with no progress data to show. */
export default function SkeletonGrid({
  label,
  head,
}: {
  label?: string;
  head?: React.ReactNode;
}) {
  return (
    <div className="skeleton">
      {head ?? (
        <div className="skeleton__status" key={label}>
          {label}
        </div>
      )}
      <div
        className="skeleton__grid"
        style={{ gridTemplateColumns: `repeat(${COLS}, 1fr)` }}
      >
        {Array.from({ length: ROWS * COLS }, (_, i) => {
          const r = Math.floor(i / COLS);
          return (
            <div
              key={i}
              className={`skeleton__cell${r === 0 ? " skeleton__cell--head" : ""}`}
              style={
                {
                  animationDelay: `${(r * 0.12).toFixed(2)}s`,
                  "--w": widthFor(i),
                  opacity: 1 - r * 0.09,
                } as React.CSSProperties
              }
            />
          );
        })}
      </div>
      {/* Single shimmer overlay — one calm sweep instead of 48 individual ones */}
      <div className="skeleton__shimmer" />
    </div>
  );
}
