import { useMemo } from "react";
import { GraphicWalker } from "@kanaries/graphic-walker";
import "@kanaries/graphic-walker/dist/style.css";

interface BIExploreProps {
  grid: (string | number)[][];
  /** Rows in the sheet, when the grid is only the drawn part of a bigger one.
   * Exploring a slice is fine; not knowing you are is not. */
  totalRows?: number;
}

export default function BIExplore({ grid, totalRows }: BIExploreProps) {
  const data = useMemo(() => {
    if (!grid || grid.length < 2) return [];

    // Duplicate or blank header cells collapse onto each other as object keys,
    // so a column silently disappears from the field list. Give every column a
    // usable, unique name instead.
    const seen = new Map<string, number>();
    const headers = grid[0].map((raw, i) => {
      let name = String(raw ?? "").trim() || `Cột ${i + 1}`;
      const before = seen.get(name);
      if (before !== undefined) {
        seen.set(name, before + 1);
        name = `${name} (${before + 1})`;
      } else {
        seen.set(name, 0);
      }
      return name;
    });

    return grid.slice(1).map((row) => {
      const obj: Record<string, string | number | null> = {};
      headers.forEach((h, i) => {
        const v = row[i];
        obj[h] = v === "" || v === undefined ? null : v;
      });
      return obj;
    });
  }, [grid]);

  if (data.length === 0) {
    return (
      <div style={{ padding: "2rem", color: "#5c6b63", textAlign: "center" }}>
        Không có dữ liệu hợp lệ để khám phá.
      </div>
    );
  }

  const showingSlice = typeof totalRows === "number" && totalRows > data.length;

  return (
    <div className="bi-explore-container animate-fade-in">
      {showingSlice && (
        <div className="bi-explore-note">
          Đang khám phá <strong>{data.length.toLocaleString("vi-VN")}</strong> dòng đầu
          trong tổng số <strong>{totalRows.toLocaleString("vi-VN")}</strong> dòng — mọi
          con số dưới đây tính trên phần này, không phải toàn bộ bảng.
        </div>
      )}
      <div className="bi-explore-canvas">
        <GraphicWalker
          data={data}
          // The app is light-themed. Left unset, graphic-walker follows the OS
          // setting and renders a dark panel inside a light page.
          appearance="light"
          style={{ width: "100%", height: "100%" }}
        />
      </div>
    </div>
  );
}
