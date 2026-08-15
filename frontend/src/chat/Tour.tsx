import { useCallback, useEffect, useLayoutEffect, useState } from "react";

export interface TourStep {
  /** Value of the `data-tour` attribute to spotlight. Omit for a centered
   * step (welcome / closing) that isn't about a specific control. */
  anchor?: string;
  title: string;
  body: string;
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

  const step = steps[i];
  const isLast = i === steps.length - 1;

  const sync = useCallback(() => setRect(readRect(step?.anchor)), [step?.anchor]);

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
      if (e.key === "Escape") onFinish();
      else if (e.key === "Enter" || e.key === "ArrowRight") next();
      else if (e.key === "ArrowLeft") setI((v) => Math.max(0, v - 1));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [next, onFinish]);

  if (!step) return null;
  const pos = cardPosition(rect);

  return (
    <div
      className={`tour${rect ? "" : " tour--plain"}`}
      role="dialog"
      aria-modal="true"
      aria-label="Hướng dẫn sử dụng"
    >
      {/* Transparent full-screen catcher: the dimming itself comes from the
          spotlight's outward shadow, which is paint-only and never receives
          clicks - without this, every click on the "dimmed" area would fall
          straight through into the app while the tour is still open.

          Deliberately NOT a dismiss target. The tour auto-opens on first visit
          and being dismissed is remembered forever, so a single stray click
          anywhere on the page would silently cost the user the whole
          onboarding. Leaving is explicit: the "Bỏ qua" button or Esc. */}
      <div className="tour__catcher" />

      {/* One element does the ring AND the dimming, so the cut-out can never
          drift out of sync the way four separate panels would. Paint-only:
          it must not steal the click from the control it is pointing at. */}
      {rect && (
        <div
          className="tour__spot"
          style={{ top: rect.top, left: rect.left, width: rect.width, height: rect.height }}
        />
      )}

      <div className="tour__card" style={{ top: pos.top, left: pos.left, width: CARD_W }}>
        <div className="tour__step-count">
          Bước {i + 1}/{steps.length}
        </div>
        <h3 className="tour__title">{step.title}</h3>
        <p className="tour__body">{step.body}</p>
        <div className="tour__actions">
          <button className="tour__skip" onClick={onFinish}>
            Bỏ qua
          </button>
          <div className="tour__nav">
            {i > 0 && (
              <button className="button button--small button--secondary" onClick={() => setI(i - 1)}>
                Quay lại
              </button>
            )}
            <button className="button button--small button--primary" onClick={next}>
              {isLast ? "Bắt đầu dùng" : "Tiếp"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
