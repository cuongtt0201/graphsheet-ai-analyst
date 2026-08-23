/** Stylesheet for the standalone deck document.
 *
 * Kept as a string rather than a .css file on purpose: it is inlined into a
 * document that must work with no network and no host page, whether that is an
 * iframe, a saved file, or a print preview.
 */
export const DECK_CSS = String.raw`
/* ── Slide deck ──────────────────────────────────────────────────────────
   Rendered as HTML because the browser is the only part of the pipeline that
   knows how wide a Vietnamese sentence actually is. Four rules keep a deck
   from breaking, and none of them is "hope the text is short":

   1. Nothing is positioned by coordinate. Every layout is flex or grid, so a
      long line grows its own container instead of landing on the box beside it.
   2. Type is sized in cqi (percent of the slide's width), not px. The slide is
      one scalable object, so it reads the same in a small panel or full screen.
   3. clamp() puts a floor and a ceiling on every size, so a short deck does not
      look cartoonish and a long one does not become unreadable.
   4. Where text could still exceed its space, line-clamp cuts it visibly with
      an ellipsis. Failing where you can see it beats overlapping silently. */

.deck {
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: 100%;
  min-width: 0;
}
.deck__stage {
  container-type: inline-size;
  width: 100%;
}
.slide {
  /* One 16:9 box. Everything inside sizes off its width. */
  aspect-ratio: 16 / 9;
  width: 100%;
  display: flex;
  flex-direction: column;
  padding: 5cqi 6cqi 3.4cqi;
  border-radius: 14px;
  background: linear-gradient(150deg, #0f172a 0%, #142033 55%, #10231f 100%);
  color: #f8fafc;
  overflow: hidden;
  box-shadow: 0 10px 30px rgba(15, 23, 42, 0.28);
}
.slide__body {
  flex: 1 1 auto;
  min-height: 0;          /* lets children shrink instead of pushing the foot out */
  display: flex;
  flex-direction: column;
  gap: 2.2cqi;
  overflow: hidden;
}
.slide__body--title,
.slide__body--section,
.slide__body--big { justify-content: center; }

.slide__kicker {
  margin: 0;
  font-size: clamp(9px, 1.5cqi, 15px);
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: #10b981;
}
.slide__title {
  margin: 0;
  font-size: clamp(20px, 5.4cqi, 54px);
  line-height: 1.14;
  font-weight: 800;
  /* Three lines is the most a title can take before it stops being a title. */
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
  overflow: hidden;
}
.slide__lede {
  margin: 0;
  font-size: clamp(11px, 2.1cqi, 21px);
  line-height: 1.5;
  color: #94a3b8;
}
.slide__section-heading {
  margin: 0;
  font-size: clamp(18px, 4.6cqi, 46px);
  line-height: 1.2;
  font-weight: 700;
}
.slide__heading {
  margin: 0;
  flex: 0 0 auto;
  font-size: clamp(14px, 3.1cqi, 31px);
  line-height: 1.25;
  font-weight: 700;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
}

/* KPI row: auto-fit means 2 KPIs get wide cards and 4 get narrow ones, with no
   count-specific rules and no chance of a fifth card wrapping into the void. */
.slide__kpis {
  flex: 1 1 auto;
  min-height: 0;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(0, 1fr));
  gap: 2.4cqi;
  align-content: center;
}
.slide__kpi {
  display: flex;
  flex-direction: column;
  gap: 0.7cqi;
  padding: 2.4cqi 2cqi;
  border-radius: 10px;
  background: rgba(148, 163, 184, 0.1);
  border-left: 3px solid #10b981;
  min-width: 0;
}
.slide__kpi-value {
  font-size: clamp(16px, 4.2cqi, 42px);
  font-weight: 800;
  line-height: 1.05;
  color: #10b981;
  overflow-wrap: anywhere;   /* a long number breaks rather than widens the card */
}
.slide__kpi-label {
  font-size: clamp(9px, 1.7cqi, 17px);
  line-height: 1.35;
  color: #cbd5e1;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
}
.slide__kpi-note {
  font-size: clamp(8px, 1.4cqi, 14px);
  color: #94a3b8;
}

.slide__chart {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  background: rgba(248, 250, 252, 0.96);
  border-radius: 10px;
  padding: 1.4cqi;
}
.slide__chart > * { width: 100%; max-height: 100%; }

.slide__split {
  flex: 1 1 auto;
  min-height: 0;
  display: grid;
  grid-template-columns: 1.35fr 1fr;
  gap: 3cqi;
  align-items: center;
}

.slide__bullets,
.slide__actions {
  margin: 0;
  padding-left: 1.2em;
  display: flex;
  flex-direction: column;
  gap: 1.6cqi;
  font-size: clamp(10px, 2cqi, 20px);
  line-height: 1.45;
  color: #e2e8f0;
  min-width: 0;
  overflow: hidden;
}
.slide__bullets--lead { flex: 1 1 auto; justify-content: center; }
.slide__bullets li,
.slide__actions li {
  /* Two lines per bullet. Past that it is a paragraph, not a bullet. */
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
  overflow-wrap: anywhere;
}
.slide__actions li::marker { color: #10b981; font-weight: 700; }

.slide__takeaway {
  flex: 0 0 auto;
  margin: 0;
  padding-top: 1.6cqi;
  border-top: 1px solid rgba(148, 163, 184, 0.28);
  font-size: clamp(10px, 1.9cqi, 19px);
  line-height: 1.45;
  color: #f8fafc;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
}

.slide__big-value {
  margin: 0;
  font-size: clamp(34px, 12cqi, 120px);
  font-weight: 800;
  line-height: 1;
  color: #10b981;
  overflow-wrap: anywhere;
}
.slide__big-caption {
  margin: 0;
  font-size: clamp(11px, 2.2cqi, 22px);
  line-height: 1.45;
  color: #cbd5e1;
}

.slide__foot {
  flex: 0 0 auto;
  display: flex;
  justify-content: space-between;
  gap: 2cqi;
  padding-top: 1.6cqi;
  font-size: clamp(8px, 1.3cqi, 13px);
  color: #64748b;
}
.slide__foot span:first-child {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.deck__controls {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.deck__dots { display: flex; gap: 5px; flex-wrap: wrap; justify-content: center; }
.deck__dot {
  width: 7px;
  height: 7px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: #cbd5e1;
  cursor: pointer;
}
.deck__dot--active { background: #10b981; transform: scale(1.35); }

/* The document stands alone inside the iframe, so it brings its own reset. */
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; height: 100%; background: #0b1220; }
body {
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 10px;
}
.deck { max-width: 100%; max-height: 100%; }
.slide[hidden] { display: none; }
.deck__nav {
  border: 1px solid rgba(148,163,184,0.35);
  background: rgba(148,163,184,0.12);
  color: #e2e8f0;
  font-size: 13px;
  padding: 4px 12px;
  border-radius: 999px;
  cursor: pointer;
}
.deck__nav:disabled { opacity: 0.35; cursor: default; }
.deck__dot { background: rgba(148,163,184,0.45); }

/* Ctrl+P from inside the frame prints every slide, one per page. */
@media print {
  @page { size: 1600px 900px; margin: 0; }
  body { display: block; padding: 0; background: #fff; }
  .deck__controls { display: none; }
  .slide { border-radius: 0; box-shadow: none; page-break-after: always; }
  .slide[hidden] { display: flex !important; }
}
`;
