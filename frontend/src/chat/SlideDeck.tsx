import { useMemo, useRef, useState } from "react";
import type { ChartSpec } from "../api";
import { buildDeckHtml } from "./deckHtml";
import { DECK_CSS } from "./deckCss";

export interface DeckSlide {
  layout:
    | "title" | "section" | "kpi" | "chart" | "chart_split" | "two_charts"
    | "bullets" | "big_number" | "quote" | "compare" | "timeline" | "closing";
  kicker?: string;
  heading?: string;
  takeaway?: string;
  bullets?: string[];
  kpis?: { label: string; value: string; note?: string }[];
  big_value?: string;
  big_caption?: string;
  chart_index?: number;
  chart_index_b?: number;
  /** compare: columns side by side. timeline: milestones in order. */
  items?: { label: string; value: string; note?: string }[];
}

export interface Deck {
  title: string;
  subtitle?: string;
  slides: DeckSlide[];
  charts?: ChartSpec[];
}


/** Hosts a deck as a sandboxed document, the way an artifact is hosted.
 *
 * The deck is built into one complete HTML string and handed to an iframe via
 * srcdoc, with `sandbox` granting scripts and nothing else. No same-origin, so
 * the document cannot reach into the app, read its storage or its cookies; the
 * app cannot leak styles into the document either. That isolation is what makes
 * it safe to put model-authored presentation code on screen at all.
 *
 * The same string is what the download button saves, so the file the user keeps
 * is byte-for-byte the deck they just looked at.
 */
export default function SlideDeck({ deck }: { deck: Deck }) {
  const frameRef = useRef<HTMLIFrameElement>(null);
  const [saved, setSaved] = useState(false);

  const html = useMemo(() => buildDeckHtml(deck, DECK_CSS), [deck]);

  if (!deck.slides?.length) return null;

  const download = () => {
    const url = URL.createObjectURL(new Blob([html], { type: "text/html;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${(deck.title || "bai-thuyet-trinh").replace(/[\/:*?"<>|]/g, "-").slice(0, 60)}.html`;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => {
      URL.revokeObjectURL(url);
      a.remove();
    }, 0);
    setSaved(true);
    setTimeout(() => setSaved(false), 2200);
  };

  return (
    <div className="deck-host">
      <iframe
        ref={frameRef}
        className="deck-host__frame"
        title={deck.title}
        srcDoc={html}
        // Scripts, and deliberately nothing else: no allow-same-origin, so the
        // document runs in its own opaque origin.
        sandbox="allow-scripts"
      />
      <div className="deck-host__bar">
        <span className="deck-host__title" title={deck.title}>🎞️ {deck.title}</span>
        <span className="deck-host__count">{deck.slides.length} slide</span>
        <button
          className="button button--small button--secondary"
          onClick={() => frameRef.current?.requestFullscreen?.()}
        >
          ⛶ Toàn màn hình
        </button>
        <button className="button button--small button--secondary" onClick={download}>
          {saved ? "✓ Đã tải" : "⬇ Tải .html"}
        </button>
      </div>
    </div>
  );
}
