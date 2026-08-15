/** The waiting screen, answering three different questions in three places.
 *
 * The old version put one self-replacing line above a ghost grid. Text changed
 * faster than it could be read, nothing said how much was left, and the richest
 * signal the backend produces — the model's own thought summaries — was hidden
 * in a collapsed log that only appeared AFTER the wait was over.
 *
 *   how much longer?   the stage rail
 *   what is it doing?  the thought line, centred
 *   is it really on?   the engine strip: model, tokens, seconds
 *
 * Everything here is driven by structured fields (`stage`, `kind`, engine
 * events), never by pattern-matching message prose.
 */

export const STAGES = ["Đọc tệp", "Hiểu cấu trúc", "Phân tích", "Dựng bảng"];

export interface EngineState {
  model?: string;
  provider?: string;
  tokensIn?: number;
  tokensOut?: number;
  secs?: number;
  /** How many slots this answer has been through. >1 means the pool re-routed. */
  attempt?: number;
  /** True while a slot is rate-limited and the pool is moving to another. */
  busy?: boolean;
}

export interface WorkBenchProps {
  stage: number;
  /** Latest human-readable step, whatever its kind. */
  message: string;
  /** Thought summaries in arrival order; the last one is shown large. */
  thoughts: string[];
  engine: EngineState;
}

const fmt = (n?: number) => (n == null ? null : n.toLocaleString("vi-VN"));

/** Thought summaries arrive as raw markdown — "**Developing Analytical
 *  Questions**" was rendering with its asterisks visible. Strip emphasis rather
 *  than render it: this line is already styled, and a bold-inside-bold heading
 *  would only fight the surrounding type. */
const clean = (s: string) =>
  s.replace(/\*\*(.+?)\*\*/g, "$1").replace(/[*_`#]/g, "").trim();

export default function WorkBench({ stage, message, thoughts, engine }: WorkBenchProps) {
  const latest = thoughts.length ? thoughts[thoughts.length - 1] : "";
  // Two previous thoughts drift above the current one. More than that turns the
  // centre of the screen into a log, which is what the console view is for.
  const older = thoughts.slice(-3, -1);

  return (
    <div className="wb">
      <div className="wb__rail" role="progressbar" aria-valuemin={1} aria-valuemax={STAGES.length}
           aria-valuenow={stage + 1} aria-label={`Bước ${stage + 1}: ${STAGES[stage] ?? ""}`}>
        {STAGES.map((name, i) => (
          <div className="wb__seg" key={name}>
            {i > 0 && (
              <span className="wb__link">
                <b style={{ width: i <= stage ? "100%" : "0%" }} />
              </span>
            )}
            <div className={`wb__stg${i < stage ? " is-done" : i === stage ? " is-live" : ""}`}>
              <span className="wb__dot">{i < stage ? "✓" : i + 1}</span>
              <span className="wb__lbl">{name}</span>
            </div>
          </div>
        ))}
      </div>

      <div className="wb__mid" aria-live="polite">
        {older.map((t, i) => (
          <p className={`wb__past${i === 0 && older.length > 1 ? " is-faded" : ""}`} key={`${t}-${i}`}>
            {clean(t)}
          </p>
        ))}
        {latest ? (
          <p className="wb__think" key={latest}>💭 {clean(latest)}</p>
        ) : (
          <p className="wb__think wb__think--plain" key={message}>{message}</p>
        )}
        {/* When a thought is showing, the step line still matters — it says what
            the thought is FOR. Kept small so it never competes. */}
        {latest && message && <p className="wb__sub">{message}</p>}
        <span className="wb__pulse" aria-hidden="true"><i /><i /><i /></span>
      </div>

      <div className="wb__engine">
        {engine.model ? (
          <span className={`wb__chip${engine.busy ? " is-warn" : " is-live"}`}>
            <i className="wb__bead" />
            {engine.model}
          </span>
        ) : (
          <span className="wb__chip">đang kết nối…</span>
        )}
        {engine.tokensIn != null && (
          <span className="wb__chip">
            token <b>{fmt(engine.tokensIn)}</b> → <b>{fmt(engine.tokensOut ?? 0)}</b>
          </span>
        )}
        {engine.secs != null && <span className="wb__chip"><b>{engine.secs.toFixed(1)}</b>s</span>}
        {/* A re-route is the system working around a jam, not a fault — showing
            it turns an unexplained pause into visible evidence of that. */}
        {(engine.attempt ?? 1) > 1 && (
          <span className="wb__chip is-warn">đổi model · lần {engine.attempt}</span>
        )}
      </div>
    </div>
  );
}
