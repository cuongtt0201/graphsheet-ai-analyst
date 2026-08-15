import { useEffect, useRef } from "react";
import { createUniver, defaultTheme, LocaleType, merge } from "@univerjs/presets";
import { UniverSheetsCorePreset } from "@univerjs/preset-sheets-core";
import sheetsCoreViVN from "@univerjs/preset-sheets-core/locales/vi-VN";
import "@univerjs/preset-sheets-core/lib/index.css";

export interface GridSheet {
  name: string;
  /** Full matrix of cells (no separate header row) - exactly as in the file. */
  grid: (string | number)[][];
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

/** What the user will actually see, used only to size the column. */
function displayLength(v: string | number): number {
  if (typeof v === "number" && Number.isFinite(v)) {
    return v.toLocaleString("vi-VN", { maximumFractionDigits: 2 }).length;
  }
  return String(v ?? "").length;
}

interface Cell {
  v: string | number;
  s?: { n: { pattern: string } };
}

function gridToSheet(id: string, name: string, grid: (string | number)[][]) {
  const cellData: Record<number, Record<number, Cell>> = {};
  const widest: number[] = [];
  let maxCols = 1;

  grid.forEach((row, r) => {
    cellData[r] = {};
    row.forEach((val, col) => {
      const v = val ?? "";
      cellData[r][col] = needsPattern(v)
        ? { v, s: { n: { pattern: NUM_PATTERN } } }
        : { v };
      widest[col] = Math.max(widest[col] ?? 0, displayLength(v));
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
  };
}

/** Renders exactly ONE sheet with Univer. The parent (ChatWorkspace) owns the
 * sheet/tab navigation and lazy-loads each sheet's grid before passing it here,
 * so the payload is always one sheet at a time regardless of how many sheets a
 * file has. We recreate the instance when the shown sheet changes - simple and
 * correct; a single sheet is cheap to build. */
export default function UniverGrid({ sheet }: { sheet: GridSheet | null }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const signature = sheet ? `${sheet.name}:${sheet.grid.length}` : "empty";

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
      sheets: { s0: gridToSheet("s0", sheetName(data.name), data.grid) },
    });

    return () => univer.dispose();
  }, [signature]);

  return <div ref={containerRef} className="univer-host" />;
}
