import { useCallback, useEffect, useLayoutEffect, useState } from "react";

export interface TourStep {
  /** Value of the `data-tour` attribute to spotlight. Omit for a centered
   * step (welcome / closing) that isn't about a specific control. */
  anchor?: string;
  title: string;
  body: string;
  badge?: string;
  onEnter?: () => void;
  actionButton?: {
    label: string;
    loadingLabel?: string;
    onClick: () => Promise<boolean | void> | boolean | void;
  };
}

interface Rect {
  top: number;
  left: number;
  width: number;
  height: number;
}

const PAD = 8;
const CARD_W = 320;
const GAP = 14;

function readRect(anchor?: string): Rect | null {
  if (!anchor) return null;
  const el = document.querySelector<HTMLElement>(`[data-tour="${anchor}"]`);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  // A control that is rendered but collapsed/hidden has no usable box; treat
  // it as absent so the step degrades to a centered explanation instead of
  // spotlighting a 0x0 spot in the corner.
  if (r.width < 2 || r.height < 2) return null;
  return { top: r.top - PAD, left: r.left - PAD, width: r.width + PAD * 2, height: r.height + PAD * 2 };
}

/** Where to put the card so it stays on screen: below the anchor when there is
 * room, above when there isn't, centered when there is no anchor at all. */
function cardPosition(rect: Rect | null): { top: number; left: number } {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  if (!rect) {
    return { top: Math.max(vh / 2 - 120, 16), left: Math.max(vw / 2 - CARD_W / 2, 16) };
  }
  const below = rect.top + rect.height + GAP;
  const fitsBelow = below + 200 < vh;
  const top = fitsBelow ? below : Math.max(rect.top - 200 - GAP, 16);
  const left = Math.min(Math.max(rect.left, 16), vw - CARD_W - 16);
  return { top, left };
}

export default function Tour({ steps, onFinish }: { steps: TourStep[]; onFinish: () => void }) {
  const [i, setI] = useState(0);
  const [rect, setRect] = useState<Rect | null>(null);
  const [actionLoading, setActionLoading] = useState(false);

  const step = steps[i];
  const isLast = i === steps.length - 1;

  const sync = useCallback(() => setRect(readRect(step?.anchor)), [step?.anchor]);

  // Execute optional onEnter trigger for this step (e.g. switching tabs or preview states)
  useEffect(() => {
    step?.onEnter?.();
  }, [i, step]);

  // useLayoutEffect so the spotlight is measured before paint - with a plain
  // effect the highlight visibly jumps from the previous step's position.
  useLayoutEffect(sync, [sync]);

  // An anchor scrolled out of view would be spotlighted off-screen, leaving the
  // user staring at a dimmed page with no visible highlight.
  useEffect(() => {
    if (!step?.anchor) return;
    const el = document.querySelector<HTMLElement>(`[data-tour="${step.anchor}"]`);
    if (!el) return;
    el.scrollIntoView({ block: "center", inline: "nearest", behavior: "smooth" });
    // Re-measure after the smooth scroll settles; the rect during the scroll is
    // the pre-scroll one and would leave the ring behind.
    const t = window.setTimeout(sync, 320);
    return () => window.clearTimeout(t);
  }, [step?.anchor, sync]);

  useEffect(() => {
    window.addEventListener("resize", sync);
    // capture:true so scrolling INSIDE any pane (the chat log, the grid) also
    // repositions the spotlight, not just window-level scrolling.
    window.addEventListener("scroll", sync, true);
    return () => {
      window.removeEventListener("resize", sync);
      window.removeEventListener("scroll", sync, true);
    };
  }, [sync]);

  const next = useCallback(() => {
    if (isLast) onFinish();
    else setI((v) => v + 1);
  }, [isLast, onFinish]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (actionLoading) return;
      if (e.key === "Escape") onFinish();
      else if (e.key === "Enter" || e.key === "ArrowRight") next();
      else if (e.key === "ArrowLeft") setI((v) => Math.max(0, v - 1));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [next, onFinish, actionLoading]);

  if (!step) return null;
  const pos = cardPosition(rect);

  return (
    <div
      className={`tour${rect ? "" : " tour--plain"}`}
      role="dialog"
      aria-modal="true"
      aria-label="Hướng dẫn sử dụng"
    >
      <div className="tour__catcher" />

      {rect && (
        <div
          className="tour__spot"
          style={{ top: rect.top, left: rect.left, width: rect.width, height: rect.height }}
        />
      )}

      <div className="tour__card" style={{ top: pos.top, left: pos.left, width: CARD_W }}>
        <div className="tour__step-count" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span>Bước {i + 1}/{steps.length}</span>
          {step.badge && (
            <span
              style={{
                background: "rgba(16, 185, 129, 0.15)",
                color: "#34d399",
                padding: "1px 7px",
                borderRadius: "4px",
                fontSize: "0.68rem",
                fontWeight: 600,
                border: "1px solid rgba(16, 185, 129, 0.3)",
                letterSpacing: "0.02em",
              }}
            >
              {step.badge}
            </span>
          )}
        </div>
        <h3 className="tour__title">{step.title}</h3>
        <p className="tour__body">{step.body}</p>

        {step.actionButton && (
          <div style={{ marginBottom: "0.85rem" }}>
            <button
              className="button button--small button--primary"
              style={{
                width: "100%",
                padding: "0.5rem 0.75rem",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                gap: "6px",
                fontWeight: 600,
                fontSize: "0.84rem",
                background: "linear-gradient(135deg, #10b981, #059669)",
                borderColor: "#10b981",
                boxShadow: "0 2px 8px rgba(16, 185, 129, 0.35)",
              }}
              disabled={actionLoading}
              onClick={async () => {
                if (actionLoading) return;
                setActionLoading(true);
                try {
                  const res = await step.actionButton!.onClick();
                  if (res !== false) {
                    next();
                  }
                } finally {
                  setActionLoading(false);
                }
              }}
            >
              {actionLoading
                ? step.actionButton.loadingLabel || "Đang xử lý..."
                : step.actionButton.label}
            </button>
          </div>
        )}

        <div className="tour__actions">
          <button className="tour__skip" onClick={onFinish} disabled={actionLoading}>
            Bỏ qua
          </button>
          <div className="tour__nav">
            {i > 0 && (
              <button
                className="button button--small button--secondary"
                onClick={() => setI(i - 1)}
                disabled={actionLoading}
              >
                Quay lại
              </button>
            )}
            <button
              className="button button--small button--primary"
              onClick={next}
              disabled={actionLoading}
            >
              {isLast ? "Bắt đầu dùng" : "Tiếp"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
