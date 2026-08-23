import { renderToStaticMarkup } from "react-dom/server.browser";
import { createElement } from "react";
import MiniChart from "./MiniChart";
import type { ChartSpec } from "../api";
import type { Deck, DeckSlide } from "./SlideDeck";

/** Build ONE self-contained HTML document for a deck.
 *
 * Self-contained is the point. The document carries its own stylesheet and its
 * own navigation script and references nothing outside itself, so the same
 * string can be dropped into a sandboxed iframe, saved as a .html file that
 * works offline, or printed to PDF -- three deliverables from one renderer.
 *
 * It also means the deck's CSS can never collide with the app's, in either
 * direction. Inside the iframe there is no app stylesheet to leak in and no
 * chance a slide rule leaks out.
 */

const ESCAPES: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

/** Deck text is model-authored, so it is data, never markup. */
function esc(value: unknown): string {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ESCAPES[c]);
}

/** MiniChart is pure SVG with no runtime dependencies, so it renders to static
 *  markup that stays crisp at any size and needs no script inside the iframe. */
function chartSvg(spec: ChartSpec | undefined): string {
  if (!spec) return "";
  try {
    return renderToStaticMarkup(createElement(MiniChart, { spec, showTitle: false }));
  } catch {
    return "";
  }
}

function bulletList(items: string[] | undefined, cls: string, tag: "ul" | "ol" = "ul"): string {
  const rows = (items ?? []).map((b) => `<li>${esc(b)}</li>`).join("");
  return rows ? `<${tag} class="${cls}">${rows}</${tag}>` : "";
}

function slideBody(slide: DeckSlide, charts: ChartSpec[]): string {
  const chart = slide.chart_index != null ? charts[slide.chart_index] : undefined;
  const kicker = slide.kicker ? `<p class="slide__kicker">${esc(slide.kicker)}</p>` : "";
  const heading = slide.heading ? `<h2 class="slide__heading">${esc(slide.heading)}</h2>` : "";
  const takeaway = slide.takeaway ? `<p class="slide__takeaway">${esc(slide.takeaway)}</p>` : "";

  switch (slide.layout) {
    case "title":
      return `<div class="slide__body slide__body--title">${kicker}
        <h1 class="slide__title">${esc(slide.heading)}</h1>
        ${slide.takeaway ? `<p class="slide__lede">${esc(slide.takeaway)}</p>` : ""}</div>`;

    case "section":
      return `<div class="slide__body slide__body--section">${kicker}
        <h2 class="slide__section-heading">${esc(slide.heading)}</h2></div>`;

    case "big_number":
      return `<div class="slide__body slide__body--big">
        ${slide.heading ? `<p class="slide__kicker">${esc(slide.heading)}</p>` : ""}
        <p class="slide__big-value">${esc(slide.big_value)}</p>
        ${slide.big_caption ? `<p class="slide__big-caption">${esc(slide.big_caption)}</p>` : ""}</div>`;

    case "kpi": {
      const cards = (slide.kpis ?? []).map((k) => `<div class="slide__kpi">
        <span class="slide__kpi-value">${esc(k.value)}</span>
        <span class="slide__kpi-label">${esc(k.label)}</span>
        ${k.note ? `<span class="slide__kpi-note">${esc(k.note)}</span>` : ""}</div>`).join("");
      return `<div class="slide__body">${heading}<div class="slide__kpis">${cards}</div></div>`;
    }

    case "chart":
      return `<div class="slide__body">${heading}
        <div class="slide__chart">${chartSvg(chart)}</div>${takeaway}</div>`;

    case "chart_split":
      return `<div class="slide__body">${heading}
        <div class="slide__split">
          <div class="slide__chart">${chartSvg(chart)}</div>
          ${bulletList(slide.bullets, "slide__bullets")}
        </div>${takeaway}</div>`;

    case "two_charts": {
      const b = slide.chart_index_b != null ? charts[slide.chart_index_b] : undefined;
      return `<div class="slide__body">${heading}
        <div class="slide__pair">
          <div class="slide__chart">${chartSvg(chart)}</div>
          <div class="slide__chart">${chartSvg(b)}</div>
        </div>${takeaway}</div>`;
    }

    case "quote":
      return `<div class="slide__body slide__body--quote">
        ${kicker}<p class="slide__quote">${esc(slide.takeaway)}</p>
        ${slide.heading ? `<p class="slide__quote-by">${esc(slide.heading)}</p>` : ""}</div>`;

    case "compare": {
      const cols = (slide.items ?? []).map((it) => `<div class="slide__col">
        <span class="slide__col-label">${esc(it.label)}</span>
        <span class="slide__col-value">${esc(it.value)}</span>
        ${it.note ? `<span class="slide__col-note">${esc(it.note)}</span>` : ""}</div>`).join("");
      return `<div class="slide__body">${heading}<div class="slide__cols">${cols}</div>${takeaway}</div>`;
    }

    case "timeline": {
      const steps = (slide.items ?? []).map((it) => `<li class="slide__step">
        <span class="slide__step-label">${esc(it.label)}</span>
        <span class="slide__step-value">${esc(it.value)}</span>
        ${it.note ? `<span class="slide__step-note">${esc(it.note)}</span>` : ""}</li>`).join("");
      return `<div class="slide__body">${heading}<ol class="slide__steps">${steps}</ol>${takeaway}</div>`;
    }

    case "closing":
      return `<div class="slide__body slide__body--closing">
        <h2 class="slide__heading">${esc(slide.heading || "Đề xuất hành động")}</h2>
        ${bulletList(slide.bullets, "slide__actions", "ol")}</div>`;

    default:
      return `<div class="slide__body">${heading}
        ${bulletList(slide.bullets, "slide__bullets slide__bullets--lead")}${takeaway}</div>`;
  }
}

export function buildDeckHtml(deck: Deck, css: string): string {
  const charts = deck.charts ?? [];
  const slides = deck.slides.map((slide, i) => `<article class="slide" data-i="${i}">
      ${slideBody(slide, charts)}
      <footer class="slide__foot"><span>${esc(deck.title)}</span><span>${i + 1} / ${deck.slides.length}</span></footer>
    </article>`).join("");

  const dots = deck.slides.map((_, i) =>
    `<button class="deck__dot" data-go="${i}" title="Slide ${i + 1}"></button>`).join("");

  // No framework inside: a few lines of plain DOM is all navigation needs, and
  // it keeps the saved file openable anywhere.
  const script = `
    var slides = Array.prototype.slice.call(document.querySelectorAll('.slide'));
    var dots = Array.prototype.slice.call(document.querySelectorAll('.deck__dot'));
    var at = 0;
    function show(i) {
      at = Math.max(0, Math.min(slides.length - 1, i));
      slides.forEach(function (s, n) { s.hidden = n !== at; });
      dots.forEach(function (d, n) { d.classList.toggle('deck__dot--active', n === at); });
      document.querySelector('[data-prev]').disabled = at === 0;
      document.querySelector('[data-next]').disabled = at === slides.length - 1;
    }
    document.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowRight' || e.key === 'PageDown' || e.key === ' ') { show(at + 1); e.preventDefault(); }
      if (e.key === 'ArrowLeft' || e.key === 'PageUp') { show(at - 1); e.preventDefault(); }
    });
    document.addEventListener('click', function (e) {
      var t = e.target.closest('[data-go],[data-prev],[data-next]');
      if (!t) return;
      if (t.hasAttribute('data-prev')) show(at - 1);
      else if (t.hasAttribute('data-next')) show(at + 1);
      else show(parseInt(t.getAttribute('data-go'), 10));
    });
    show(0);`;

  return `<!doctype html>
<html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(deck.title)}</title>
<style>${css}</style></head>
<body><div class="deck">
  <div class="deck__stage">${slides}</div>
  <div class="deck__controls">
    <button class="deck__nav" data-prev>‹ Trước</button>
    <div class="deck__dots">${dots}</div>
    <button class="deck__nav" data-next>Sau ›</button>
  </div>
</div>
<script>${script}<\/script></body></html>`;
}
