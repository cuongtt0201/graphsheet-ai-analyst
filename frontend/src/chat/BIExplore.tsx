import { useMemo } from "react";
import { GraphicWalker } from "@kanaries/graphic-walker";
import "@kanaries/graphic-walker/dist/style.css";

interface BIExploreProps {
  grid: (string | number)[][];
}

export default function BIExplore({ grid }: BIExploreProps) {
  const data = useMemo(() => {
    if (!grid || grid.length < 2) return [];
    const headers = grid[0].map(String);
    const rows = grid.slice(1);
    return rows.map((row) => {
      const obj: Record<string, any> = {};
      headers.forEach((h, i) => {
        obj[h] = row[i];
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

  return (
    <div className="bi-explore-container animate-fade-in" style={{ width: "100%", height: "100%", overflow: "hidden" }}>
      <GraphicWalker
        data={data}
        style={{ width: "100%", height: "100%" }}
      />
    </div>
  );
}
