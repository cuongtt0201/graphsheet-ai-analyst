import { useCallback, useEffect, useState } from "react";
import MiniChart from "./MiniChart";
import type { ChartSpec } from "../api";

export interface DeckSlide {
  layout: "title" | "section" | "kpi" | "chart" | "chart_split" | "bullets" | "big_number" | "closing";
  kicker?: string;
  heading?: string;
  takeaway?: string;
  bullets?: string[];
  kpis?: { label: string; value: string; note?: string }[];
  big_value?: string;
  big_caption?: string;
  chart_index?: number;
}

export interface Deck {
  title: string;
  subtitle?: string;
  slides: DeckSlide[];
  charts?: ChartSpec[];
}

/** One 16:9 slide. Nothing here is positioned by coordinate: every layout is a
 *  flex or grid arrangement, so a longer Vietnamese sentence pushes its own
 *  container instead of landing on top of the box next to it. */
function SlideBody({ slide, charts }: { slide: DeckSlide; charts: ChartSpec[] }) {
  const chart = slide.chart_index != null ? charts[slide.chart_index] : undefined;

  switch (slide.layout) {
    case "title":
      return (
        <div className="slide__body slide__body--title">
          {slide.kicker && <p className="slide__kicker">{slide.kicker}</p>}
          <h1 className="slide__title">{slide.heading}</h1>
          {slide.takeaway && <p className="slide__lede">{slide.takeaway}</p>}
        </div>
      );

    case "section":
      return (
        <div className="slide__body slide__body--section">
          {slide.kicker && <p className="slide__kicker">{slide.kicker}</p>}
          <h2 className="slide__section-heading">{slide.heading}</h2>
        </div>
      );

    case "big_number":
      return (
        <div className="slide__body slide__body--big">
          {slide.heading && <p className="slide__kicker">{slide.heading}</p>}
          <p className="slide__big-value">{slide.big_value}</p>
          {slide.big_caption && <p className="slide__big-caption">{slide.big_caption}</p>}
        </div>
      );

    case "kpi":
      return (
        <div className="slide__body">
          {slide.heading && <h2 className="slide__heading">{slide.heading}</h2>}
          {/* auto-fit rather than a fixed column count: two KPIs get two wide
              cards, four get four narrow ones, and neither overflows. */}
          <div className="slide__kpis">
            {(slide.kpis ?? []).map((k, i) => (
              <div key={i} className="slide__kpi">
                <span className="slide__kpi-value">{k.value}</span>
                <span className="slide__kpi-label">{k.label}</span>
                {k.note && <span className="slide__kpi-note">{k.note}</span>}
              </div>
            ))}
          </div>
        </div>
      );

    case "chart":
      return (
        <div className="slide__body">
          {slide.heading && <h2 className="slide__heading">{slide.heading}</h2>}
          <div className="slide__chart">{chart && <MiniChart spec={chart} showTitle={false} />}</div>
          {slide.takeaway && <p className="slide__takeaway">{slide.takeaway}</p>}
        </div>
      );

    case "chart_split":
      return (
        <div className="slide__body">
          {slide.heading && <h2 className="slide__heading">{slide.heading}</h2>}
          <div className="slide__split">
            <div className="slide__chart">{chart && <MiniChart spec={chart} showTitle={false} />}</div>
            <ul className="slide__bullets">
              {(slide.bullets ?? []).map((b, i) => <li key={i}>{b}</li>)}
            </ul>
          </div>
          {slide.takeaway && <p className="slide__takeaway">{slide.takeaway}</p>}
        </div>
      );

    case "closing":
      return (
        <div className="slide__body slide__body--closing">
          <h2 className="slide__heading">{slide.heading || "Đề xuất hành động"}</h2>
          <ol className="slide__actions">
            {(slide.bullets ?? []).map((b, i) => <li key={i}>{b}</li>)}
          </ol>
        </div>
      );

    case "bullets":
    default:
      return (
        <div className="slide__body">
          {slide.heading && <h2 className="slide__heading">{slide.heading}</h2>}
          <ul className="slide__bullets slide__bullets--lead">
            {(slide.bullets ?? []).map((b, i) => <li key={i}>{b}</li>)}
          </ul>
          {slide.takeaway && <p className="slide__takeaway">{slide.takeaway}</p>}
        </div>
      );
  }
}

export default function SlideDeck({ deck }: { deck: Deck }) {
  const [index, setIndex] = useState(0);
  const total = deck.slides.length;
  const charts = deck.charts ?? [];

  const go = useCallback(
    (delta: number) => setIndex((i) => Math.min(total - 1, Math.max(0, i + delta))),
    [total]
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowRight" || e.key === "PageDown") go(1);
      if (e.key === "ArrowLeft" || e.key === "PageUp") go(-1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [go]);

  if (total === 0) return null;
  const slide = deck.slides[index];

  return (
    <div className="deck">
      <div className="deck__stage">
        {/* The 16:9 box is sized by aspect-ratio and every font inside is in
            container-query units, so the whole slide scales as one piece. */}
        <article className="slide" key={index}>
          <SlideBody slide={slide} charts={charts} />
          <footer className="slide__foot">
            <span>{deck.title}</span>
            <span>{index + 1} / {total}</span>
          </footer>
        </article>
      </div>

      <div className="deck__controls">
        <button className="button button--small button--secondary" disabled={index === 0} onClick={() => go(-1)}>
          ‹ Trước
        </button>
        <div className="deck__dots">
          {deck.slides.map((_, i) => (
            <button
              key={i}
              className={`deck__dot${i === index ? " deck__dot--active" : ""}`}
              onClick={() => setIndex(i)}
              title={`Slide ${i + 1}`}
            />
          ))}
        </div>
        <button className="button button--small button--secondary" disabled={index === total - 1} onClick={() => go(1)}>
          Sau ›
        </button>
      </div>
    </div>
  );
}
