import { useEffect, useRef } from "react";
import { createUniver, defaultTheme, LocaleType, merge } from "@univerjs/presets";
import { UniverSheetsCorePreset } from "@univerjs/preset-sheets-core";
import sheetsCoreViVN from "@univerjs/preset-sheets-core/locales/vi-VN";
import "@univerjs/preset-sheets-core/lib/index.css";

/** One cell the copilot asked to tint, in grid coordinates. */
export interface CellHighlight {
  row: number;
  col: number;
  /** Hex fill, e.g. "#FEE2E2". */
  bg?: string;
  /** Hex text colour. */
  color?: string;
  bold?: boolean;
}

/** How one column should be displayed, decided server-side from the column's
 *  role and the unit the semantics pass recorded. */
export type ColumnFormat = "currency" | "percent" | "id" | "plain";

export interface GridSheet {
  name: string;
  /** Full matrix of cells (no separate header row) - exactly as in the file. */
  grid: (string | number)[][];
  /** Column name -> format. Keyed by name, matched against the header row, so
   * it survives the copilot inserting a column. */
  columnFormats?: Record<string, ColumnFormat>;
  /** Index into `grid` of the row holding the column names, when it is known.
   * Messy business files rarely start at row 0 - the detector reports where it
   * actually is, and showing that is what makes "sửa dòng tiêu đề" legible. */
  headerRow?: number | null;
  highlights?: CellHighlight[];
}

/** Excel/Univer sheet name: <=31 chars, no \ / ? * [ ] :. */
function sheetName(raw: string): string {
  return raw.replace(/[\\/?*[\]:]/g, " ").trim().slice(0, 28) || "Sheet";
}

/** Cells wide enough that a number is never shown with its high-order digits
 *  clipped off. Univer's default column is ~88px; a value like
 *  103100904.76190476 does not fit, and because numbers align right the part
 *  that gets cut is the LEADING digits — the cell then reads "04.76190476",
 *  which is not a truncated number but a different one. A spreadsheet may show
 *  a value awkwardly; it must never show a wrong value. */
const CHAR_PX = 7.6;
const PAD_PX = 20;
const MIN_W = 82;
const MAX_W = 280;

/** Float noise: a summed currency column comes back as 103100904.76190476.
 *  The extra digits are an artefact of floating-point addition, not precision
 *  anyone asked for, so they are formatted away — while `v` keeps the exact
 *  value, so copying the cell or building a formula on it still gets the full
 *  number. Integers are left alone on purpose: a numfmt with separators would
 *  turn a year 2026 into "2.026" and an id 10023 into "10.023". */
const NUM_PATTERN = "#,##0.##";
const needsPattern = (v: unknown): v is number =>
  typeof v === "number" && Number.isFinite(v) && !Number.isInteger(v);

/** Money always gets separators, including whole amounts: 1500000 is unreadable
 *  and 1,500,000 is not. This is safe here only because the server decided the
 *  column is currency - applying it to every integer would render a year 2026
 *  as "2,026" and an order code 10023 as "10,023". */
const CURRENCY_PATTERN = "#,##0.##";
const PERCENT_PATTERN = "0.0%";

const ROW_STRIPE_BG = "#F7FAF9";
const GRID_LINE = "#E1E7E4";
const BORDER_THIN = 1;

/** What the user will actually see, used only to size the column. */
function displayLength(v: string | number, format?: ColumnFormat): number {
  if (typeof v === "number" && Number.isFinite(v)) {
    if (format === "percent") {
      // "0.0%" renders 0.856 as "85.6%" - four characters wider than the raw
      // value, so sizing on the raw value would clip it.
      return (v * 100).toLocaleString("vi-VN", { maximumFractionDigits: 1 }).length + 1;
    }
    return v.toLocaleString("vi-VN", { maximumFractionDigits: 2 }).length;
  }
  return String(v ?? "").length;
}

/** Univer style keys: bl bold (0/1), bg background, cl colour, ht horizontal
 *  align (2 = centre), n number format. */
interface BorderSide {
  s: number;
  cl: { rgb: string };
}

interface CellStyle {
  n?: { pattern: string };
  bl?: 0 | 1;
  bg?: { rgb: string };
  cl?: { rgb: string };
  ht?: number;
  bd?: { b?: BorderSide; r?: BorderSide };
}

interface Cell {
  v: string | number;
  s?: CellStyle;
}

const HEADER_BG = "#E7F3EF";
const HEADER_FG = "#0F3D2E";
const HORIZONTAL_CENTER = 2;

function gridToSheet(
  id: string,
  name: string,
  grid: (string | number)[][],
  headerRow?: number | null,
  highlights?: CellHighlight[],
  columnFormats?: Record<string, ColumnFormat>,
) {
  const cellData: Record<number, Record<number, Cell>> = {};
  const widest: number[] = [];
  let maxCols = 1;

  // Resolve name-keyed formats to column indices via the header row. Keying by
  // name rather than position is what keeps them correct after the copilot
  // inserts a column.
  const formatOf: (ColumnFormat | undefined)[] = [];
  if (columnFormats && headerRow != null && grid[headerRow]) {
    grid[headerRow].forEach((cell, col) => {
      formatOf[col] = columnFormats[String(cell ?? "").trim()];
    });
  }

  const border: BorderSide = { s: BORDER_THIN, cl: { rgb: GRID_LINE } };

  // Index the highlights once; a rule can touch every row of a long sheet and
  // scanning the list per cell would be quadratic.
  const tinted = new Map<string, CellHighlight>();
  for (const h of highlights ?? []) tinted.set(`${h.row}:${h.col}`, h);

  grid.forEach((row, r) => {
    cellData[r] = {};
    row.forEach((val, col) => {
      const v = val ?? "";
      const style: CellStyle = {};
      const isHeader = headerRow != null && r === headerRow;
      const format = isHeader ? undefined : formatOf[col];

      if (format === "currency" && typeof v === "number") {
        style.n = { pattern: CURRENCY_PATTERN };
      } else if (format === "percent" && typeof v === "number") {
        style.n = { pattern: PERCENT_PATTERN };
      } else if (format !== "id" && needsPattern(v)) {
        style.n = { pattern: NUM_PATTERN };
      }

      // Ruled lines make a wide table scannable across a row.
      style.bd = { b: border, r: border };

      if (isHeader) {
        style.bl = 1;
        style.bg = { rgb: HEADER_BG };
        style.cl = { rgb: HEADER_FG };
        style.ht = HORIZONTAL_CENTER;
      } else if (headerRow != null && r > headerRow && (r - headerRow) % 2 === 0) {
        // Stripe every other DATA row, counted from the header, so the banding
        // does not flip when a file has preamble rows above it.
        style.bg = { rgb: ROW_STRIPE_BG };
      }
      const hit = tinted.get(`${r}:${col}`);
      if (hit) {
        // A highlight wins over the header fill: the user asked for this one.
        if (hit.bg) style.bg = { rgb: hit.bg };
        if (hit.color) style.cl = { rgb: hit.color };
        if (hit.bold) style.bl = 1;
      }
      cellData[r][col] = { v, s: style };
      widest[col] = Math.max(widest[col] ?? 0, displayLength(v, format));
    });
    maxCols = Math.max(maxCols, row.length);
  });

  const columnData: Record<number, { w: number }> = {};
  for (let col = 0; col < maxCols; col++) {
    const chars = widest[col] ?? 0;
    columnData[col] = {
      w: Math.min(MAX_W, Math.max(MIN_W, Math.round(chars * CHAR_PX + PAD_PX))),
    };
  }

  return {
    id,
    name,
    rowCount: Math.max(grid.length + 5, 20),
    columnCount: Math.max(maxCols + 2, 10),
    cellData,
    columnData,
    // Keep the header on screen while scrolling. Freezing through the header
    // row also pins whatever preamble rows sit above it, which is what a reader
    // of a messy file wants anyway.
    ...(headerRow != null
      ? { freeze: { xSplit: 0, ySplit: headerRow + 1, startRow: headerRow + 1, startColumn: 0 } }
      : {}),
  };
}

/** Renders exactly ONE sheet with Univer. The parent (ChatWorkspace) owns the
 * sheet/tab navigation and lazy-loads each sheet's grid before passing it here,
 * so the payload is always one sheet at a time regardless of how many sheets a
 * file has. We recreate the instance when the shown sheet changes - simple and
 * correct; a single sheet is cheap to build. */
export default function UniverGrid({
  sheet,
  revision = 0,
}: {
  sheet: GridSheet | null;
  /** Bump to force a rebuild when the grid changed in a way the signature
   * below cannot see -- a copilot edit that rewrites cell values in place. */
  revision?: number;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  // Column count belongs in the signature: adding a column leaves the row count
  // untouched, so a rows-only signature left the new column invisible until the
  // user switched tabs and back.
  const signature = sheet
    ? [
        sheet.name,
        `${sheet.grid.length}x${sheet.grid[0]?.length ?? 0}`,
        sheet.headerRow ?? "-",
        sheet.highlights?.length ?? 0,
        Object.keys(sheet.columnFormats ?? {}).length,
        revision,
      ].join(":")
    : "empty";

  useEffect(() => {
    if (!containerRef.current) return;

    const data = sheet ?? { name: "Trống", grid: [["Chưa có dữ liệu"]] };
    const { univer, univerAPI } = createUniver({
      locale: LocaleType.VI_VN,
      locales: { [LocaleType.VI_VN]: merge({}, sheetsCoreViVN) },
      theme: defaultTheme,
      presets: [UniverSheetsCorePreset({ container: containerRef.current })],
    });

    univerAPI.createWorkbook({
      id: "wb",
      name: data.name,
      sheetOrder: ["s0"],
      sheets: {
        s0: gridToSheet(
          "s0",
          sheetName(data.name),
          data.grid,
          data.headerRow,
          data.highlights,
          data.columnFormats,
        ),
      },
    });

    return () => univer.dispose();
  }, [signature]);

  return <div ref={containerRef} className="univer-host" />;
}
