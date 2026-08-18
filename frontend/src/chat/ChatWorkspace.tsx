import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  type ChatReply,
  type ChatTurn,
  type ChartSpec,
  type ExecutiveReport,
  type FileProfile,
  type LiveAgentEvent,
  type LiveChart,
  type LiveKpi,
  type ChartRole,
  type ChartSize,
  type DashboardFilters,
  type SavedReport,
  type TableResult,
} from "../api";
import UniverGrid, { type GridSheet } from "./UniverGrid";
import Tour, { type TourStep } from "./Tour";
import MiniChart from "./MiniChart";
import SkeletonGrid from "./SkeletonGrid";
import WorkBench, { type EngineState } from "./WorkBench";
import BIExplore from "./BIExplore";
import AuthBar from "./AuthBar";

const Logo = () => (
  <svg className="graphsheet-logo-svg" viewBox="0 0 32 32" width="28" height="28" style={{ marginRight: '10px' }}>
    <defs>
      <linearGradient id="logo-grad" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0%" stopColor="#312e81" />
        <stop offset="100%" stopColor="#0f172a" />
      </linearGradient>
    </defs>
    {/* Grid background */}
    <rect x="2" y="2" width="28" height="28" rx="6" fill="url(#logo-grad)" stroke="rgba(255,255,255,0.15)" strokeWidth="1.5" />
    <path d="M2 10h28M2 18h28M2 26h28M10 2v28M18 2v28M26 2v28" stroke="rgba(255,255,255,0.08)" strokeWidth="1" />
    {/* Graph nodes and edges */}
    <line x1="10" y1="10" x2="18" y2="18" stroke="#a78bfa" strokeWidth="2" />
    <line x1="18" y1="18" x2="26" y2="10" stroke="#a78bfa" strokeWidth="2" />
    <line x1="18" y1="18" x2="10" y2="26" stroke="#60a5fa" strokeWidth="2" />
    <circle cx="10" cy="10" r="3.5" fill="#f43f5e" style={{ filter: 'drop-shadow(0 0 3px #f43f5e)' }} />
    <circle cx="18" cy="18" r="4" fill="#a78bfa" style={{ filter: 'drop-shadow(0 0 4px #a78bfa)' }} />
    <circle cx="26" cy="10" r="3" fill="#34d399" style={{ filter: 'drop-shadow(0 0 3px #34d399)' }} />
    <circle cx="10" cy="26" r="3" fill="#60a5fa" style={{ filter: 'drop-shadow(0 0 3px #60a5fa)' }} />
  </svg>
);

interface ErrorBoundaryState {
  error: Error | null;
}

/** Catches render-time crashes in the sheet/BI view so one bad render
 * doesn't unmount and blank the entire app (there was no boundary before,
 * so any exception here took the whole page down to a blank screen). */
class ViewErrorBoundary extends React.Component<{ children: React.ReactNode }, ErrorBoundaryState> {
  constructor(props: { children: React.ReactNode }) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error("View crashed:", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="chat-empty" style={{ flex: 1 }}>
          <p>⚠️ Không hiển thị được phần này do lỗi: {this.state.error.message}</p>
          <button className="button button--small" onClick={() => this.setState({ error: null })}>
            Thử lại
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

function renderInlineMarkdown(text: string) {
  const parts: React.ReactNode[] = [];
  let remaining = text;
  let keyIdx = 0;

  while (remaining) {
    const boldIdx = remaining.indexOf("**");
    const codeIdx = remaining.indexOf("`");

    if (boldIdx === -1 && codeIdx === -1) {
      parts.push(<span key={keyIdx++}>{remaining}</span>);
      break;
    }

    if (boldIdx !== -1 && (codeIdx === -1 || boldIdx < codeIdx)) {
      if (boldIdx > 0) {
        parts.push(<span key={keyIdx++}>{remaining.substring(0, boldIdx)}</span>);
      }
      const nextBold = remaining.indexOf("**", boldIdx + 2);
      if (nextBold !== -1) {
        parts.push(
          <strong key={keyIdx++} className="md-bold" style={{ fontWeight: 700, color: "#ffffff" }}>
            {remaining.substring(boldIdx + 2, nextBold)}
          </strong>
        );
        remaining = remaining.substring(nextBold + 2);
      } else {
        parts.push(<span key={keyIdx++}>{remaining.substring(boldIdx)}</span>);
        break;
      }
    } else {
      if (codeIdx > 0) {
        parts.push(<span key={keyIdx++}>{remaining.substring(0, codeIdx)}</span>);
      }
      const nextCode = remaining.indexOf("`", codeIdx + 1);
      if (nextCode !== -1) {
        parts.push(
          <code key={keyIdx++} className="md-inline-code" style={{ fontFamily: "var(--font-mono)", background: "rgba(255, 255, 255, 0.12)", padding: "1px 4px", borderRadius: "4px", color: "#60a5fa" }}>
            {remaining.substring(codeIdx + 1, nextCode)}
          </code>
        );
        remaining = remaining.substring(nextCode + 1);
      } else {
        parts.push(<span key={keyIdx++}>{remaining.substring(codeIdx)}</span>);
        break;
      }
    }
  }

  return parts;
}

function preprocessText(text: string): string {
  if (!text) return "";
  let processed = text;
  
  // Replace space-separated numbers like " 1. ", " 2. ", " 3. "
  // with a newline before them so they render on separate lines.
  // Using negative lookbehind or simple matching to avoid matching decimals (like 3.14).
  processed = processed.replace(/(?:\s+)(\d+\.\s+)/g, "\n$1");

  // Also handle bullet lists like " - ", " * ", " • "
  processed = processed.replace(/(?:\s+)([-\*•]\s+)/g, "\n$1");

  return processed;
}

function MarkdownText({ text }: { text: string }) {
  if (!text) return null;
  const processed = preprocessText(text);
  const paragraphs = processed.split("\n\n");
  
  return (
    <>
      {paragraphs.map((p, pIdx) => {
        const lines = p.split("\n");
        const renderedElements: React.ReactNode[] = [];
        let currentListItems: { marker: string; text: string }[] = [];
        let listType: "decimal" | "bullet" = "decimal";

        const flushList = (key: string) => {
          if (currentListItems.length > 0) {
            renderedElements.push(
              <ul
                key={key}
                className="md-list"
                style={{
                  margin: "8px 0",
                  paddingLeft: "24px",
                  listStyleType: listType === "decimal" ? "decimal" : "disc",
                }}
              >
                {currentListItems.map((item, idx) => (
                  <li key={idx} style={{ marginBottom: "6px" }}>
                    {renderInlineMarkdown(item.text)}
                  </li>
                ))}
              </ul>
            );
            currentListItems = [];
          }
        };

        lines.forEach((line, lIdx) => {
          const trimmed = line.trim();
          const listMatch = trimmed.match(/^(\d+\.|\*|-|•)\s+(.*)/);
          
          if (listMatch) {
            const marker = listMatch[1];
            const content = listMatch[2];
            const isDecimal = /^\d+\./.test(marker);
            
            const newType = isDecimal ? "decimal" : "bullet";
            if (currentListItems.length > 0 && listType !== newType) {
              flushList(`list-${pIdx}-${lIdx}-flush`);
            }
            
            listType = newType;
            currentListItems.push({ marker, text: content });
          } else {
            flushList(`list-${pIdx}-${lIdx}`);
            if (trimmed) {
              renderedElements.push(
                <div key={`line-${pIdx}-${lIdx}`} className="md-line" style={{ margin: "6px 0", lineHeight: 1.55 }}>
                  {renderInlineMarkdown(line)}
                </div>
              );
            }
          }
        });

        flushList(`list-${pIdx}-end`);

        return <div key={pIdx} className="md-paragraph-wrapper">{renderedElements}</div>;
      })}
    </>
  );
}

interface GraphNode {
  id: string;
  label: string;
  type: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
  radius: number;
  color: string;
}

interface GraphLink {
  source: string;
  target: string;
  label: string;
}

function MemoryGraphCanvas({ nodes: rawNodes, edges: rawEdges }: { nodes: any[]; edges: any[] }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const nodesRef = useRef<GraphNode[]>([]);
  const linksRef = useRef<GraphLink[]>([]);
  const dragNodeRef = useRef<GraphNode | null>(null);
  // Screen-space pan (x, y) + zoom (k). Lets users spread out a cluttered
  // graph and zoom in to read labels instead of being stuck at a fixed size.
  const transformRef = useRef({ x: 0, y: 0, k: 1 });
  const panStateRef = useRef<{ panning: boolean; lastX: number; lastY: number }>({ panning: false, lastX: 0, lastY: 0 });

  useEffect(() => {
    const canvas = canvasRef.current;
    const width = canvas?.clientWidth || 260;
    const height = canvas?.clientHeight || 220;

    const typeColors: Record<string, string> = {
      user: "#a78bfa",
      file: "#34d399",
      recipe: "#38bdf8",
      action: "#fb7185",
      skill: "#fbbf24",
      behavior: "#f472b6",
    };

    const typeRadii: Record<string, number> = {
      user: 14,
      file: 10,
      recipe: 9,
      action: 7,
      skill: 8,
      behavior: 9,
    };

    const nodesMap = new Map(nodesRef.current.map((n) => [n.id, n]));
    
    nodesRef.current = rawNodes.map((n) => {
      const existing = nodesMap.get(n.id);
      const radius = typeRadii[n.type] || 8;
      const color = typeColors[n.type] || "#9ca3af";
      return {
        id: n.id,
        label: n.label,
        type: n.type,
        x: existing?.x ?? (width / 2 + (Math.random() - 0.5) * width * 0.8),
        y: existing?.y ?? (height / 2 + (Math.random() - 0.5) * height * 0.8),
        vx: existing?.vx ?? 0,
        vy: existing?.vy ?? 0,
        radius,
        color,
      };
    });

    linksRef.current = rawEdges.map((e) => ({
      source: e.source,
      target: e.target,
      label: e.label,
    }));
  }, [rawNodes, rawEdges]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let animId: number;
    const runSimulation = () => {
      const width = canvas.width = canvas.clientWidth;
      const height = canvas.height = canvas.clientHeight;
      const nodes = nodesRef.current;
      const links = linksRef.current;

      const kRepulsion = 3200;
      const kLink = 0.03;
      const kGravity = 0.006;
      const damping = 0.85;

      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const n1 = nodes[i];
          const n2 = nodes[j];
          const dx = n2.x - n1.x;
          const dy = n2.y - n1.y;
          const distSq = dx * dx + dy * dy + 0.1;
          const dist = Math.sqrt(distSq);
          if (dist < 320) {
            const force = kRepulsion / distSq;
            const fx = (dx / dist) * force;
            const fy = (dy / dist) * force;
            
            if (n1 !== dragNodeRef.current) {
              n1.vx -= fx;
              n1.vy -= fy;
            }
            if (n2 !== dragNodeRef.current) {
              n2.vx += fx;
              n2.vy += fy;
            }
          }
        }
      }

      const nodesById = new Map(nodes.map((n) => [n.id, n]));
      for (const link of links) {
        const source = nodesById.get(link.source);
        const target = nodesById.get(link.target);
        if (source && target) {
          const dx = target.x - source.x;
          const dy = target.y - source.y;
          const dist = Math.sqrt(dx * dx + dy * dy) || 0.1;
          const force = (dist - 110) * kLink;
          const fx = (dx / dist) * force;
          const fy = (dy / dist) * force;

          if (source !== dragNodeRef.current) {
            source.vx += fx;
            source.vy += fy;
          }
          if (target !== dragNodeRef.current) {
            target.vx -= fx;
            target.vy -= fy;
          }
        }
      }

      const cx = width / 2;
      const cy = height / 2;
      for (const n of nodes) {
        if (n === dragNodeRef.current) continue;
        n.vx += (cx - n.x) * kGravity;
        n.vy += (cy - n.y) * kGravity;
      }

      for (const n of nodes) {
        if (n === dragNodeRef.current) continue;
        n.x += n.vx;
        n.y += n.vy;
        n.vx *= damping;
        n.vy *= damping;

        if (n.x < n.radius) { n.x = n.radius; n.vx *= -0.5; }
        if (n.x > width - n.radius) { n.x = width - n.radius; n.vx *= -0.5; }
        if (n.y < n.radius) { n.y = n.radius; n.vy *= -0.5; }
        if (n.y > height - n.radius) { n.y = height - n.radius; n.vy *= -0.5; }
      }

      ctx.clearRect(0, 0, width, height);

      const { x: panX, y: panY, k: zoom } = transformRef.current;
      ctx.save();
      ctx.translate(panX, panY);
      ctx.scale(zoom, zoom);

      ctx.strokeStyle = "rgba(255, 255, 255, 0.03)";
      ctx.lineWidth = 1 / zoom;
      const gridSize = 20;
      for (let x = 0; x < width; x += gridSize) {
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, height);
        ctx.stroke();
      }
      for (let y = 0; y < height; y += gridSize) {
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(width, y);
        ctx.stroke();
      }

      ctx.strokeStyle = "rgba(255, 255, 255, 0.15)";
      ctx.lineWidth = 1.5 / zoom;
      for (const link of links) {
        const source = nodesById.get(link.source);
        const target = nodesById.get(link.target);
        if (source && target) {
          ctx.beginPath();
          ctx.moveTo(source.x, source.y);
          ctx.lineTo(target.x, target.y);
          ctx.stroke();
        }
      }

      for (const n of nodes) {
        ctx.shadowColor = n.color;
        ctx.shadowBlur = 8;
        ctx.fillStyle = n.color;
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.radius, 0, Math.PI * 2);
        ctx.fill();

        ctx.shadowBlur = 0;
        ctx.fillStyle = "#0c1017";
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.radius - 2.5, 0, Math.PI * 2);
        ctx.fill();

        ctx.fillStyle = "#f0f6fc";
        ctx.font = "bold 9px sans-serif";
        ctx.textAlign = "center";
        let text = n.label ?? "";
        if (text.length > 18) text = text.substring(0, 15) + "...";
        ctx.fillText(text, n.x, n.y + n.radius + 12);
      }

      ctx.restore();

      animId = requestAnimationFrame(runSimulation);
    };

    runSimulation();
    return () => cancelAnimationFrame(animId);
  }, []);

  // Zoom with the mouse wheel, centered on the cursor. Registered as a native
  // (non-passive) listener because React's onWheel is passive by default and
  // can't call preventDefault to stop the page from scrolling underneath.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      const t = transformRef.current;
      const worldX = (mx - t.x) / t.k;
      const worldY = (my - t.y) / t.k;
      const nextK = Math.min(4, Math.max(0.25, t.k * (e.deltaY > 0 ? 0.9 : 1.1)));
      transformRef.current = {
        k: nextK,
        x: mx - worldX * nextK,
        y: my - worldY * nextK,
      };
    };
    canvas.addEventListener("wheel", onWheel, { passive: false });
    return () => canvas.removeEventListener("wheel", onWheel);
  }, []);

  const toWorld = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current!;
    const rect = canvas.getBoundingClientRect();
    const sx = e.clientX - rect.left;
    const sy = e.clientY - rect.top;
    const t = transformRef.current;
    return { x: (sx - t.x) / t.k, y: (sy - t.y) / t.k, sx, sy };
  };

  const handleMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!canvasRef.current) return;
    const { x, y, sx, sy } = toWorld(e);

    const clicked = nodesRef.current.find((n) => {
      const dx = n.x - x;
      const dy = n.y - y;
      return dx * dx + dy * dy < (n.radius + 10) * (n.radius + 10);
    });

    if (clicked) {
      dragNodeRef.current = clicked;
      clicked.vx = 0;
      clicked.vy = 0;
    } else {
      panStateRef.current = { panning: true, lastX: sx, lastY: sy };
    }
  };

  const handleMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (dragNodeRef.current) {
      const { x, y } = toWorld(e);
      dragNodeRef.current.x = x;
      dragNodeRef.current.y = y;
      return;
    }
    if (panStateRef.current.panning) {
      const canvas = canvasRef.current;
      if (!canvas) return;
      const rect = canvas.getBoundingClientRect();
      const sx = e.clientX - rect.left;
      const sy = e.clientY - rect.top;
      const { lastX, lastY } = panStateRef.current;
      transformRef.current = {
        ...transformRef.current,
        x: transformRef.current.x + (sx - lastX),
        y: transformRef.current.y + (sy - lastY),
      };
      panStateRef.current = { panning: true, lastX: sx, lastY: sy };
    }
  };

  const handleMouseUpOrLeave = () => {
    dragNodeRef.current = null;
    panStateRef.current.panning = false;
  };

  return (
    <div className="graph-container">
      <canvas
        ref={canvasRef}
        className="graph-canvas"
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUpOrLeave}
        onMouseLeave={handleMouseUpOrLeave}
      />
      <div className="graph-legend">
        <div className="legend-item"><span className="legend-dot" style={{ background: "#a78bfa" }}></span> Ta</div>
        <div className="legend-item"><span className="legend-dot" style={{ background: "#34d399" }}></span> Tệp</div>
        <div className="legend-item"><span className="legend-dot" style={{ background: "#38bdf8" }}></span> Dashboard</div>
        <div className="legend-item"><span className="legend-dot" style={{ background: "#fb7185" }}></span> Thao tác</div>
        <div className="legend-item"><span className="legend-dot" style={{ background: "#fbbf24" }}></span> Kỹ năng</div>
        <div className="legend-item"><span className="legend-dot" style={{ background: "#f472b6" }}></span> Ghi nhớ</div>
        <div className="legend-hint">Cuộn để zoom · Kéo nền để di chuyển · Kéo nút để sắp xếp</div>
      </div>
    </div>
  );
}

// Versioned: bumping the key re-shows the tour once for users.
const TOUR_SEEN_KEY = "gs_tour_seen_v4";

const DEMO_GRID_DATA: (string | number)[][] = [
  ["Ngày", "Mã Đơn", "Khu Vực", "Cửa Hàng", "Nhân Viên", "Nhóm Hàng", "Tên Sản Phẩm", "Số Lượng", "Đơn Giá", "Thành Tiền", "Kênh Bán"],
  ["2024-01-05", "DH1001", "Hà Nội", "Chi nhánh Cầu Giấy", "Nguyễn Văn An", "Đồ uống", "Cà phê Espresso", 2, 35000, 70000, "Tại quầy"],
  ["2024-01-05", "DH1002", "TP. Hồ Chí Minh", "Chi nhánh Quận 1", "Trần Thị Mai", "Đồ uống", "Trà Đào Cam Sả", 3, 45000, 135000, "GrabFood"],
  ["2024-01-06", "DH1003", "Đà Nẵng", "Chi nhánh Hải Châu", "Lê Hoàng Nam", "Bánh ngọt", "Bánh Croissant", 4, 32000, 128000, "Tại quầy"],
  ["2024-01-07", "DH1004", "TP. Hồ Chí Minh", "Chi nhánh Quận 3", "Phạm Thu Trang", "Đồ uống", "Cà phê Sữa Đá", 5, 29000, 145000, "ShopeeFood"],
  ["2024-01-08", "DH1005", "Hà Nội", "Chi nhánh Hoàn Kiếm", "Đỗ Minh Quân", "Cà phê hạt", "Hạt Arabica Cầu Đất (500g)", 2, 180000, 360000, "Shopee"],
  ["2024-01-10", "DH1006", "Cần Thơ", "Chi nhánh Ninh Kiều", "Vũ Hải Đăng", "Đồ uống", "Trà Sữa Matcha", 4, 55000, 220000, "Tại quầy"],
  ["2024-01-12", "DH1007", "Hà Nội", "Chi nhánh Cầu Giấy", "Nguyễn Văn An", "Đồ ăn nhẹ", "Combo Bữa Sáng", 3, 65000, 195000, "Tại quầy"],
  ["2024-01-15", "DH1008", "TP. Hồ Chí Minh", "Chi nhánh Quận 1", "Trần Thị Mai", "Cà phê hạt", "Hạt Robusta Honey (500g)", 3, 145000, 435000, "Website"],
  ["2024-01-18", "DH1009", "Hải Phòng", "Chi nhánh Hồng Bàng", "Bùi Hồng Phúc", "Đồ uống", "Cà phê Espresso", 6, 35000, 210000, "Tại quầy"],
  ["2024-01-20", "DH1010", "Đà Nẵng", "Chi nhánh Hải Châu", "Lê Hoàng Nam", "Đồ uống", "Trà Đào Cam Sả", 5, 45000, 225000, "Tại quầy"],
  ["2024-01-22", "DH1011", "TP. Hồ Chí Minh", "Chi nhánh Quận 3", "Phạm Thu Trang", "Bánh ngọt", "Bánh Tiramisu", 4, 48000, 192000, "GrabFood"],
  ["2024-01-25", "DH1012", "Hà Nội", "Chi nhánh Hoàn Kiếm", "Đỗ Minh Quân", "Đồ uống", "Trà Sen Vàng", 8, 49000, 392000, "Tại quầy"],
  ["2024-02-02", "DH1013", "Cần Thơ", "Chi nhánh Ninh Kiều", "Vũ Hải Đăng", "Đồ uống", "Cà phê Sữa Đá", 10, 29000, 290000, "Tại quầy"],
  ["2024-02-05", "DH1014", "Hà Nội", "Chi nhánh Cầu Giấy", "Nguyễn Văn An", "Bánh ngọt", "Bánh Croissant", 6, 32000, 192000, "GrabFood"],
  ["2024-02-08", "DH1015", "TP. Hồ Chí Minh", "Chi nhánh Quận 1", "Trần Thị Mai", "Đồ ăn nhẹ", "Combo Bữa Sáng", 5, 65000, 325000, "Tại quầy"],
  ["2024-02-12", "DH1016", "Đà Nẵng", "Chi nhánh Hải Châu", "Lê Hoàng Nam", "Cà phê hạt", "Hạt Arabica Cầu Đất (500g)", 4, 180000, 720000, "Website"],
  ["2024-02-15", "DH1017", "TP. Hồ Chí Minh", "Chi nhánh Quận 3", "Phạm Thu Trang", "Đồ uống", "Trà Sữa Matcha", 7, 55000, 385000, "ShopeeFood"],
  ["2024-02-18", "DH1018", "Hải Phòng", "Chi nhánh Hồng Bàng", "Bùi Hồng Phúc", "Đồ uống", "Trà Đào Cam Sả", 8, 45000, 360000, "Tại quầy"],
  ["2024-02-22", "DH1019", "Hà Nội", "Chi nhánh Hoàn Kiếm", "Đỗ Minh Quân", "Đồ uống", "Cà phê Sữa Đá", 12, 29000, 348000, "ShopeeFood"],
  ["2024-02-26", "DH1020", "Cần Thơ", "Chi nhánh Ninh Kiều", "Vũ Hải Đăng", "Bánh ngọt", "Bánh Tiramisu", 5, 48000, 240000, "Tại quầy"],
  ["2024-03-01", "DH1021", "TP. Hồ Chí Minh", "Chi nhánh Quận 1", "Trần Thị Mai", "Đồ uống", "Cà phê Espresso", 15, 35000, 525000, "Tại quầy"],
  ["2024-03-05", "DH1022", "Hà Nội", "Chi nhánh Cầu Giấy", "Nguyễn Văn An", "Đồ uống", "Trà Sen Vàng", 10, 49000, 490000, "GrabFood"],
  ["2024-03-08", "DH1023", "Đà Nẵng", "Chi nhánh Hải Châu", "Lê Hoàng Nam", "Đồ ăn nhẹ", "Combo Bữa Sáng", 8, 65000, 520000, "Tại quầy"],
  ["2024-03-12", "DH1024", "TP. Hồ Chí Minh", "Chi nhánh Quận 3", "Phạm Thu Trang", "Cà phê hạt", "Hạt Robusta Honey (500g)", 6, 145000, 870000, "Website"],
  ["2024-03-15", "DH1025", "Hải Phòng", "Chi nhánh Hồng Bàng", "Bùi Hồng Phúc", "Đồ uống", "Trà Sữa Matcha", 9, 55000, 495000, "Tại quầy"],
  ["2024-03-18", "DH1026", "Hà Nội", "Chi nhánh Hoàn Kiếm", "Đỗ Minh Quân", "Bánh ngọt", "Bánh Croissant", 10, 32000, 320000, "Tại quầy"],
  ["2024-03-22", "DH1027", "Cần Thơ", "Chi nhánh Ninh Kiều", "Vũ Hải Đăng", "Đồ uống", "Trà Đào Cam Sả", 11, 45000, 495000, "ShopeeFood"],
  ["2024-03-25", "DH1028", "TP. Hồ Chí Minh", "Chi nhánh Quận 1", "Trần Thị Mai", "Đồ uống", "Cà phê Sữa Đá", 14, 29000, 406000, "GrabFood"],
  ["2024-03-28", "DH1029", "Hà Nội", "Chi nhánh Cầu Giấy", "Nguyễn Văn An", "Cà phê hạt", "Hạt Arabica Cầu Đất (500g)", 5, 180000, 900000, "Website"],
  ["2024-03-30", "DH1030", "Đà Nẵng", "Chi nhánh Hải Châu", "Lê Hoàng Nam", "Bánh ngọt", "Bánh Tiramisu", 7, 48000, 336000, "Tại quầy"],
];

const DEMO_PROFILE: FileProfile = {
  source_id: "demo_sales_sheet",
  filename: "Doanh_Thu_Ban_Hang_Mau.csv",
  sheet: "DoanhThuBanHang",
  row_count: 30,
  columns: ["Ngày", "Mã Đơn", "Khu Vực", "Cửa Hàng", "Nhân Viên", "Nhóm Hàng", "Tên Sản Phẩm", "Số Lượng", "Đơn Giá", "Thành Tiền", "Kênh Bán"],
  dtypes: {
    "Ngày": "object",
    "Mã Đơn": "object",
    "Khu Vực": "object",
    "Cửa Hàng": "object",
    "Nhân Viên": "object",
    "Nhóm Hàng": "object",
    "Tên Sản Phẩm": "object",
    "Số Lượng": "int64",
    "Đơn Giá": "int64",
    "Thành Tiền": "int64",
    "Kênh Bán": "object",
  },
  sample_rows: [
    { "Ngày": "2024-01-05", "Mã Đơn": "DH1001", "Khu Vực": "Hà Nội", "Cửa Hàng": "Chi nhánh Cầu Giấy", "Nhân Viên": "Nguyễn Văn An", "Nhóm Hàng": "Đồ uống", "Tên Sản Phẩm": "Cà phê Espresso", "Số Lượng": "2", "Đơn Giá": "35000", "Thành Tiền": "70000", "Kênh Bán": "Tại quầy" },
  ],
  has_data: true,
  grid_rows: 30,
  grid: DEMO_GRID_DATA,
};

const DEMO_DASHBOARD_ITEMS: DashboardItem[] = [
  {
    id: "demo-kpi-1",
    type: "kpi",
    title: "Tổng Doanh Thu",
    scalar: "10,858,000 đ",
  },
  {
    id: "demo-kpi-2",
    type: "kpi",
    title: "Tổng Số Giao Dịch",
    scalar: "30 đơn hàng",
  },
  {
    id: "demo-kpi-3",
    type: "kpi",
    title: "Sản Phẩm Bán Chạy Nhất",
    scalar: "Cà phê Sữa Đá",
  },
  {
    id: "demo-chart-1",
    type: "chart",
    title: "Cơ Cấu Doanh Thu Theo Khu Vực",
    chart: {
      type: "bar",
      title: "Doanh Thu Theo Khu Vực",
      labels: ["TP. Hồ Chí Minh", "Hà Nội", "Đà Nẵng", "Hải Phòng", "Cần Thơ"],
      values: [3520000, 3180000, 1850000, 1150000, 1158000],
      series: [
        {
          name: "Doanh thu (VNĐ)",
          values: [3520000, 3180000, 1850000, 1150000, 1158000],
        },
      ],
    },
  },
  {
    id: "demo-chart-2",
    type: "chart",
    title: "Tỷ Trọng Doanh Thu Theo Nhóm Hàng",
    chart: {
      type: "pie",
      title: "Tỷ Trọng Nhóm Hàng",
      labels: ["Cà phê hạt", "Đồ uống", "Đồ ăn nhẹ", "Bánh ngọt"],
      values: [4500000, 3950000, 1200000, 1208000],
      series: [
        {
          name: "Doanh thu",
          values: [4500000, 3950000, 1200000, 1208000],
        },
      ],
    },
  },
];

interface Message {
  role: "user" | "assistant";
  reply?: ChatReply;
  text?: string;
  suggestions?: string[];
  /** Sheet-tab key of the result table this reply produced. */
  resultKey?: string;
  /** Live thought summaries collected while this answer was being produced,
   * kept so the finished bubble can still prove the work happened. */
  thoughts?: string[];
  /** Wall-clock seconds the turn took, shown next to the thought toggle. */
  thinkSecs?: number;
}

interface ResultSheet {
  key: string;
  name: string;
  table: TableResult;
}

interface DashboardItem {
  id: string;
  type: "kpi" | "chart";
  title: string;
  scalar?: string | number;
  chart?: ChartSpec;
  /** Previous-period figure for the same measure, so the KPI card can show a
   * delta. A bare number has no meaning until it sits next to a reference. */
  compareValue?: number;
  compareLabel?: string;
  /** Composition hints from the AI: which zone this belongs to and how wide. */
  role?: ChartRole;
  size?: ChartSize;
}

function layoutChartToSpec(c: LiveChart): ChartSpec {
  if (c.type === "vega" && c.vegaLiteSpec) {
    return { type: "vega", title: c.title, labels: [], values: [], vegaLiteSpec: c.vegaLiteSpec };
  }
  const data = c.data || [];
  return {
    type: c.type || "bar",
    title: c.title,
    labels: c.labels ?? data.map((d) => d.label),
    values: data.map((d) => d.value),
    series: c.series,
    points: c.points,
    matrix: c.matrix,
    rowLabels: c.rowLabels,
    target: c.target,
    max: c.max,
  };
}

const nf = new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 2 });
const fmtCell = (v: string | number): string => (typeof v === "number" ? nf.format(v) : String(v));

function tableToGrid(t: TableResult): (string | number)[][] {
  return [t.columns, ...t.rows];
}

interface ChatWorkspaceProps {
  email: string;
  onLogout: () => void;
}

export default function ChatWorkspace({ email, onLogout }: ChatWorkspaceProps) {

  const [tables, setTables] = useState<FileProfile[]>([]);
  const [gridCache, setGridCache] = useState<Record<string, (string | number)[][]>>({});
  const [resultSheets, setResultSheets] = useState<ResultSheet[]>([]);
  const [activeKey, setActiveKey] = useState<string>("");
  const [loadingSheet, setLoadingSheet] = useState(false);

  // --- NotebookLM style sidebar state ---
  const [selectedSources, setSelectedSources] = useState<string[]>([]);
  const [sidebarTab, setSidebarTab] = useState<"sources" | "memory">("sources");
  const [memoryData, setMemoryData] = useState<{
    enabled: boolean;
    nodes: any[];
    edges: any[];
    skills: any[];
    behaviors?: any[];
  } | null>(null);
  const [loadingMemory, setLoadingMemory] = useState(false);

  const handleForgetBehaviors = async () => {
    if (!window.confirm("Xóa toàn bộ ghi nhớ về thói quen của bạn? AI sẽ học lại từ đầu.")) return;
    try {
      await api.deleteBehaviors();
      fetchMemoryDiagnostics();
    } catch (e) {
      alert(`Không thể xóa: ${(e as Error).message}`);
    }
  };

  const handleToggleSource = (sourceId: string) => {
    setSelectedSources((prev) =>
      prev.includes(sourceId) ? prev.filter((id) => id !== sourceId) : [...prev, sourceId]
    );
  };

  const handleSelectAllSources = () => {
    setSelectedSources(tables.map((t) => t.source_id));
  };

  const handleDeselectAllSources = () => {
    setSelectedSources([]);
  };

  const fetchMemoryDiagnostics = async () => {
    setLoadingMemory(true);
    try {
      const data = await api.diagnosticsMemory();
      setMemoryData(data);
    } catch (e) {
      console.error("Failed to load memory diagnostics:", e);
    } finally {
      setLoadingMemory(false);
    }
  };

  // --- Resizing states and handlers ---
  const [chatWidth, setChatWidth] = useState(400);
  const isResizing = useRef(false);

  const startResize = (e: React.MouseEvent) => {
    e.preventDefault();
    isResizing.current = true;
    document.addEventListener("mousemove", handleResize);
    document.addEventListener("mouseup", stopResize);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  };

  const handleResize = (e: MouseEvent) => {
    if (!isResizing.current) return;
    const newWidth = window.innerWidth - e.clientX;
    if (newWidth > 280 && newWidth < window.innerWidth * 0.75) {
      setChatWidth(newWidth);
    }
  };

  const stopResize = () => {
    isResizing.current = false;
    document.removeEventListener("mousemove", handleResize);
    document.removeEventListener("mouseup", stopResize);
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
  };

  const fetchTables = () => {
    api.tables().then((res) => {
      if (res.tables && res.tables.length > 0) {
        setTables(res.tables);
        setSelectedSources(res.tables.map(p => p.source_id));
        const cache: Record<string, (string | number)[][]> = {};
        for (const p of res.tables) {
          if (p.grid) cache[p.source_id] = p.grid;
        }
        setGridCache(cache);
        const firstActive = res.tables.find((p) => p.grid)?.source_id || res.tables[0].source_id;
        setActiveKey(firstActive);
      } else {
        setTables([]);
        setSelectedSources([]);
        setActiveKey("");
        setDashboardItems([]);
        setResultSheets([]);
        setDashFilters(null);
      }
    }).catch(err => console.error("Failed to load tables:", err));
  };

  const handleDeleteFile = async (filename: string) => {
    if (!window.confirm(`Bạn có chắc muốn xóa file "${filename}" khỏi phiên làm việc này không?`)) return;
    try {
      await api.deleteFile(filename);
      fetchTables();
    } catch (e) {
      alert("Lỗi khi xóa file: " + (e as Error).message);
    }
  };

  useEffect(() => {
    // Load session tables on mount
    fetchTables();

    // Restore pinned Dashboard tiles - previously only the auto-build result
    // survived a refresh; tiles pinned one-at-a-time from chat lived only in
    // this component's state and vanished on reload.
    api.getDashboardItems().then((res) => {
      if (res.items && res.items.length > 0) setDashboardItems(res.items);
    }).catch(err => console.error("Failed to load dashboard items on mount:", err));

    // The layout script lives in the session too, so a dashboard built before a
    // refresh is still filterable — without this the filter bar would only ever
    // appear in the tab that happened to build it.
    api.dashboardFilters()
      .then((f) => { if (f.can_filter) setDashFilters(f); })
      .catch(() => {});

    return () => {
      document.removeEventListener("mousemove", handleResize);
      document.removeEventListener("mouseup", stopResize);
    };
  }, []);

  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [statusText, setStatusText] = useState("");
  // Waiting-screen state. Kept apart from statusText because each answers a
  // different question and they update at different rates: the stage moves a
  // handful of times, thoughts stream continuously, engine facts land per call.
  const [workStage, setWorkStage] = useState(0);
  const [workThoughts, setWorkThoughts] = useState<string[]>([]);
  const [workEngine, setWorkEngine] = useState<EngineState>({});

  // --- Dashboard filtering (Grafana-style: one control bar, all panels follow) ---
  const [dashFilters, setDashFilters] = useState<DashboardFilters | null>(null);
  const [timeRange, setTimeRange] = useState<string>("all");
  const [dimFilters, setDimFilters] = useState<Record<string, string[]>>({});
  const [refiltering, setRefiltering] = useState(false);
  const [filteredRows, setFilteredRows] = useState<number | null>(null);

  // First visit opens the tour once; the ❓ button reopens it on demand.
  const [showTour, setShowTour] = useState(() => !localStorage.getItem(TOUR_SEEN_KEY));

  // Snapshot used by the Tutorial Sandbox (Lớp ảo Tour) to restore the user's workspace upon exit
  const tourSnapshotRef = useRef<{
    tables: FileProfile[];
    activeKey: string;
    gridCache: Record<string, (string | number)[][]>;
    messages: Message[];
    dashboardItems: DashboardItem[];
    sidebarTab: "sources" | "memory";
  } | null>(null);
  const isTourDemoActiveRef = useRef(false);

  const startTour = () => {
    tourSnapshotRef.current = {
      tables,
      activeKey,
      gridCache,
      messages,
      dashboardItems,
      sidebarTab,
    };
    isTourDemoActiveRef.current = false;
    setShowTour(true);
  };

  const finishTour = () => {
    if (isTourDemoActiveRef.current) {
      if (tourSnapshotRef.current && tourSnapshotRef.current.tables.length > 0) {
        setTables(tourSnapshotRef.current.tables);
        setSelectedSources(tourSnapshotRef.current.tables.map((t) => t.source_id));
        setActiveKey(tourSnapshotRef.current.activeKey);
        setGridCache(tourSnapshotRef.current.gridCache);
        setMessages(tourSnapshotRef.current.messages);
        setDashboardItems(tourSnapshotRef.current.dashboardItems);
        setSidebarTab(tourSnapshotRef.current.sidebarTab);
      } else {
        setTables([]);
        setSelectedSources([]);
        setActiveKey("");
        setGridCache({});
        setMessages([]);
        setDashboardItems([]);
        setSidebarTab("sources");
      }
      isTourDemoActiveRef.current = false;
    }
    setShowTour(false);
    localStorage.setItem(TOUR_SEEN_KEY, "1");
  };

  // --- Live chat progress (streamed from /api/chat while `busy`) ---
  const [chatStage, setChatStage] = useState("");
  const [chatReason, setChatReason] = useState("");
  const [chatThoughts, setChatThoughts] = useState<string[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);
  const logRef = useRef<HTMLDivElement>(null);

  // --- Overlay states (chart/KPI shown on the sheet side) ---
  const [displayChart, setDisplayChart] = useState<ChartSpec | null>(null);
  const [displayScalar, setDisplayScalar] = useState<number | string | null>(null);
  const [displayScalarLabel, setDisplayScalarLabel] = useState("");

  // --- Dashboard Tab state ---
  const [dashboardItems, setDashboardItems] = useState<DashboardItem[]>([]);
  // Join problems found while building the current dashboard. Kept beside the
  // dashboard rather than in the chat log, because that is where the affected
  // totals are being read.
  const [dashboardJoinWarnings, setDashboardJoinWarnings] = useState<string[]>([]);
  const [dashboardInsights, setDashboardInsights] = useState<string[]>([]);
  const [exporting, setExporting] = useState(false);
  const [viewMode, setViewMode] = useState<"grid" | "bi">("grid");

  // Presentation-only preferences (arrangement + accent color of the pinned
  // Dashboard tiles) - purely visual, no effect on data/computation. Kept in
  // localStorage so a refresh doesn't reset a choice the user made.
  type DashboardLayout = "grid" | "kpi-first" | "two-column" | "storytelling" | "overview-detail";
  type DashboardPalette = "emerald" | "ocean" | "sunset";
  const [dashboardLayout, setDashboardLayout] = useState<DashboardLayout>(
    () => (localStorage.getItem("gs_dashboard_layout") as DashboardLayout) || "grid"
  );
  const [dashboardPalette, setDashboardPalette] = useState<DashboardPalette>(
    () => (localStorage.getItem("gs_dashboard_palette") as DashboardPalette) || "emerald"
  );
  useEffect(() => { localStorage.setItem("gs_dashboard_layout", dashboardLayout); }, [dashboardLayout]);
  useEffect(() => { localStorage.setItem("gs_dashboard_palette", dashboardPalette); }, [dashboardPalette]);

  // --- One-shot "AI builds the whole dashboard" flow (Code Interpreter) ---
  const [buildingDashboard, setBuildingDashboard] = useState(false);
  const [buildStatus, setBuildStatus] = useState("");

  // --- Executive "report for the boss" (Phase 6) ---
  const [report, setReport] = useState<ExecutiveReport | null>(null);
  const [generatingReport, setGeneratingReport] = useState(false);
  const [savedReports, setSavedReports] = useState<SavedReport[]>([]);
  const [showSavedReports, setShowSavedReports] = useState(false);

  const refreshSavedReports = async () => {
    try {
      const res = await api.reportsList();
      setSavedReports(res.reports);
    } catch {
      /* session may simply have none yet */
    }
  };

  const handleDownloadReport = async (id: string) => {
    try {
      const blob = await api.downloadReport(id);
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `BaoCao_${id.slice(0, 8)}.docx`;
      document.body.appendChild(a);
      a.click();
      setTimeout(() => {
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);
      }, 250);
    } catch (e) {
      alert(`Không thể tải báo cáo: ${(e as Error).message}`);
    }
  };

  useEffect(() => {
    if (activeKey === "dashboard" || activeKey === "") {
      setViewMode("grid");
    }
  }, [activeKey]);

  // --- One-shot "AI builds the whole dashboard" (Code Interpreter) ---
  async function buildDashboardAuto(promptOverride?: string) {
    const prompt = (promptOverride ?? input).trim();
    if (buildingDashboard) return;
    if (!promptOverride) setInput("");
    setBuildingDashboard(true);
    setBuildStatus("🤖 Đang lên kế hoạch dashboard...");
    setReport(null);

    try {
      let finalKpis: LiveKpi[] = [];
      let finalCharts: LiveChart[] = [];
      let finalInsights: string[] = [];
      let suggestedLayout: string | null | undefined;
      let suggestedPalette: string | null | undefined;
      let hadError = "";

      await api.buildDashboardAuto(prompt, (e: LiveAgentEvent) => {
        if (e.type === "step") {
          setBuildStatus(e.message);
        } else if (e.type === "done") {
          finalKpis = e.kpis;
          finalCharts = e.charts;
          finalInsights = e.insights || [];
          suggestedLayout = e.suggested_layout;
          suggestedPalette = e.suggested_palette;
          setDashboardJoinWarnings(e.join_warnings || []);
        } else if (e.type === "error") {
          hadError = e.message;
        }
      }, selectedSources);

      if (hadError) throw new Error(hadError);

      const items: DashboardItem[] = [
        ...finalKpis.map((k, i) => ({
          id: `db-kpi-${Date.now()}-${i}`,
          type: "kpi" as const,
          title: k.name,
          scalar: k.value,
          compareValue: k.compare_value,
          compareLabel: k.compare_label,
        })),
        ...finalCharts.map((c, i) => ({
          id: `db-chart-${Date.now()}-${i}`,
          type: "chart" as const,
          title: c.title,
          chart: layoutChartToSpec(c),
          role: c.role,
          size: c.size,
        })),
      ];

      setDashboardItems(items);
      persistDashboardItems(items);
      setDashboardInsights(finalInsights);

      // The script behind this dashboard is now stored server-side, so the
      // filter bar can re-run it. Any selection from a previous build is stale.
      setTimeRange("all");
      setDimFilters({});
      setFilteredRows(null);
      api.dashboardFilters().then(setDashFilters).catch(() => setDashFilters(null));

      // AI's read of what this specific dashboard is ABOUT (not just item
      // counts) sets the default presentation - still just a default, the
      // dropdowns in the dashboard header let the user override it anytime.
      if (suggestedLayout) setDashboardLayout(suggestedLayout as DashboardLayout);
      if (suggestedPalette) setDashboardPalette(suggestedPalette as DashboardPalette);
      setActiveKey("dashboard");
      setMessages((m) => [
        ...m,
        { role: "assistant", text: `✅ Đã dựng xong dashboard: ${finalKpis.length} KPI, ${finalCharts.length} biểu đồ.` },
      ]);
    } catch (e) {
      setMessages((m) => [...m, { role: "assistant", text: `Lỗi dựng dashboard tự động: ${(e as Error).message}` }]);
    } finally {
      setBuildingDashboard(false);
      setBuildStatus("");
    }
  }

  async function handleGenerateReport() {
    if (generatingReport || dashboardItems.length === 0) return;
    setGeneratingReport(true);
    setActiveKey("dashboard"); // so the result is visible even when triggered from chat text
    try {
      const r = await api.report(dashboardItems);
      refreshSavedReports();
      setReport(r);
    } catch (e) {
      alert(`Không thể tạo báo cáo: ${(e as Error).message}`);
    } finally {
      setGeneratingReport(false);
    }
  }

  async function handleExportExcel() {
    if (dashboardItems.length === 0) return;
    setExporting(true);
    try {
      const blob = await api.exportExcel(dashboardItems, dashboardPalette);
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "BaoCao_Dashboard.xlsx";
      document.body.appendChild(a);
      a.click();
      setTimeout(() => {
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);
      }, 250);
    } catch (e) {
      alert(`Không thể xuất file: ${(e as Error).message}`);
    } finally {
      setExporting(false);
    }
  }

  // Keep the chat scrolled to the newest message.
  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, busy]);

  async function handleUpload(files: FileList | File[] | null, isSample?: boolean) {
    if (!files || (Array.isArray(files) ? files.length === 0 : files.length === 0)) return;
    const fileArray = Array.isArray(files) ? files : Array.from(files);
    setUploading(true);
    setStatusText(isSample ? "Đang nạp dữ liệu mẫu..." : "Đang bắt đầu tải lên...");
    setWorkStage(0);
    setWorkThoughts([]);
    setWorkEngine({});
    try {
      const res = await api.upload(
        fileArray,
        (e) => {
          if (e.type === "engine") {
            setWorkEngine((prev) => ({
              model: e.model ?? prev.model,
              provider: e.provider ?? prev.provider,
              // A "busy" or "asking" event carries no usage yet — keep the last
              // measured numbers rather than blanking the strip mid-flight.
              tokensIn: e.tokens_in ?? prev.tokensIn,
              tokensOut: e.tokens_out ?? prev.tokensOut,
              secs: e.secs ?? prev.secs,
              attempt: e.attempt ?? prev.attempt,
              busy: e.state === "busy",
            }));
            return;
          }
          if (e.type !== "step") return;
          if (typeof e.stage === "number") setWorkStage(e.stage);
          if (e.kind === "thought") {
            // Thoughts accumulate; the step line keeps showing what they are for.
            setWorkThoughts((prev) => [...prev, e.message.replace(/^💭\s*/, "")].slice(-12));
          } else {
            setStatusText(e.message);
          }
        },
        isSample
      );
      // res.files is the FULL merged session (backend appends new files to
      // the old ones instead of replacing). Pinned dashboard/results survive.
      const profiles = res.files;
      setTables(profiles);
      setSelectedSources(profiles.map((p) => p.source_id));

      // Drop cached grids: re-uploaded files may have new content, and old
      // grids re-fetch lazily via /api/sheet anyway.
      const cache: Record<string, (string | number)[][]> = {};
      for (const p of profiles) if (p.grid) cache[p.source_id] = p.grid;
      setGridCache(cache);

      const initial = res.active || profiles.find((p) => p.grid)?.source_id || profiles[0]?.source_id || "";
      setActiveKey(initial);

      const nSheets = profiles.length;
      const nFiles = new Set(profiles.map((p) => p.filename)).size;
      const fallback = `Đã nạp ${nSheets} sheet từ ${nFiles} file. Bạn muốn hỏi gì về dữ liệu này?`;
      const initialSuggestions = (res.insights?.suggestions || []).slice(0, 3);
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          text: res.insights?.summary || fallback,
          suggestions: initialSuggestions,
        },
      ]);
    } catch (e) {
      setMessages((m) => [...m, { role: "assistant", text: `Lỗi upload: ${(e as Error).message}` }]);
    } finally {
      setUploading(false);
    }
  }

  const tourSteps: TourStep[] = useMemo(
    () => [
      {
        title: "Chào mừng đến GraphSheet 👋",
        badge: "Sandbox",
        body: "Khám phá nhanh cách GraphSheet biến dữ liệu phức tạp thành bảng tính trực quan, biểu đồ và báo cáo chỉ với tiếng Việt tự nhiên.",
        onEnter: () => setSidebarTab("sources"),
      },
      {
        anchor: "upload",
        title: "1. Tải dữ liệu lên",
        badge: "Lớp ảo Demo",
        body: "Trong luồng thật, bạn bấm \"Tải file\" để chọn file Excel/CSV. Để xem thử ngay trên giao diện mà không ảnh hưởng dữ liệu, bấm nút Demo bên dưới:",
        onEnter: () => setSidebarTab("sources"),
        actionButton: {
          label: "✨ Nạp dữ liệu mẫu (Lớp ảo 0s) & tiếp tục",
          loadingLabel: "Đang bật demo...",
          onClick: () => {
            isTourDemoActiveRef.current = true;
            setTables([DEMO_PROFILE]);
            setSelectedSources(["demo_sales_sheet"]);
            setActiveKey("demo_sales_sheet");
            setGridCache({ demo_sales_sheet: DEMO_GRID_DATA });
            setMessages([
              {
                role: "assistant",
                text: "📊 [Lớp ảo Demo] Đã nạp thành công bộ dữ liệu mẫu Bán hàng chuỗi cửa hàng (30 dòng). Bạn có thể xem bảng tính trực tiếp hoặc trải nghiệm các tính năng phân tích!",
                suggestions: [
                  "Tổng doanh thu theo từng khu vực?",
                  "Top nhân viên có doanh số cao nhất?",
                  "Cơ cấu doanh thu theo nhóm hàng?",
                ],
              },
            ]);
            setDashboardItems(DEMO_DASHBOARD_ITEMS);
            setSidebarTab("sources");
          },
        },
      },
      {
        anchor: "sheet-tabs",
        title: "2. Xem dữ liệu thật trên Sheet",
        badge: "Lớp ảo Demo",
        body: "Dữ liệu được nạp hiển thị trực tiếp trên lưới bảng tính Univer Grid. Bạn có thể lọc, sắp xếp, duyệt cột và mở các bảng kết quả phân tích riêng biệt.",
        onEnter: () => {
          setSidebarTab("sources");
          setActiveKey("demo_sales_sheet");
        },
      },
      {
        anchor: "chat-input",
        title: "3. Hỏi đáp bằng tiếng Việt",
        badge: "Lớp ảo Demo",
        body: "Gõ câu hỏi như nói chuyện bình thường hoặc chọn 1 trong 3 câu hỏi gợi ý nhanh bên dưới (ví dụ: \"Top doanh thu theo khu vực\", \"Nhân viên bán chạy nhất\").",
        onEnter: () => setSidebarTab("sources"),
      },
      {
        anchor: "build-dashboard",
        title: "4. Dashboard tự động",
        badge: "Lớp ảo Demo",
        body: "Một cú bấm: AI tự lên kế hoạch phân tích, tính toán các chỉ số KPI và dựng biểu đồ trực quan toàn diện.",
        onEnter: () => setSidebarTab("sources"),
      },
      {
        anchor: "dashboard-tab",
        title: "5. Xuất báo cáo",
        badge: "Lớp ảo Demo",
        body: "Trong thẻ Dashboard, bạn có thể đổi bố cục, đổi bảng màu, tải file Excel hoàn chỉnh kèm công thức, hoặc nhờ AI viết báo cáo Word trình cấp trên.",
        onEnter: () => {
          setSidebarTab("sources");
          setActiveKey("dashboard");
          if (dashboardItems.length === 0) setDashboardItems(DEMO_DASHBOARD_ITEMS);
        },
      },
      {
        anchor: "memory-tab",
        title: "6. Càng dùng càng hiểu bạn",
        badge: "Graph Memory",
        body: "Hồng thống ghi nhớ thói quen phân tích và tự học thêm kỹ năng mới sau mỗi phiên. Vào đây xem mạng lưới ký ức Neo4j.",
        onEnter: () => setSidebarTab("memory"),
      },
      {
        title: "Xong rồi! 🎉",
        badge: "Hoàn tất",
        body: "Hướng dẫn hoàn tất! Khi bạn bấm \"Bắt đầu dùng\", toàn bộ lớp ảo demo sẽ tự động dọn dẹp để bạn bắt đầu làm việc với dữ liệu thật.",
        onEnter: () => {
          setSidebarTab("sources");
        },
      },
    ],
    [dashboardItems.length]
  );

  async function selectSheet(sourceId: string) {
    setActiveKey(sourceId);
    if (gridCache[sourceId]) return;
    setLoadingSheet(true);
    try {
      const res = await api.sheet(sourceId);
      setGridCache((c) => ({ ...c, [sourceId]: res.grid }));
    } catch (e) {
      setMessages((m) => [...m, { role: "assistant", text: `Lỗi nạp sheet: ${(e as Error).message}` }]);
    } finally {
      setLoadingSheet(false);
    }
  }

  // --- Header-row override: re-parse a sheet when auto-detection guessed wrong ---
  const [editingHeader, setEditingHeader] = useState(false);
  const [headerRowInput, setHeaderRowInput] = useState("");
  const [reparsing, setReparsing] = useState(false);

  async function handleReparse(sourceId: string, oneBasedRow: number) {
    if (reparsing || !Number.isFinite(oneBasedRow) || oneBasedRow < 1) return;
    setReparsing(true);
    try {
      const res = await api.reparse(sourceId, oneBasedRow - 1); // grid is 0-based
      setTables((prev) => prev.map((t) => (t.source_id === sourceId ? res.profile : t)));
      setEditingHeader(false);
      setMessages((m) => [
        ...m,
        { role: "assistant", text: `✅ Đã đặt dòng ${oneBasedRow} làm tiêu đề cho "${res.profile.sheet}". Cột nhận diện: ${res.profile.columns.join(", ")}` },
      ]);
    } catch (e) {
      alert(`Không phân tích lại được: ${(e as Error).message}`);
    } finally {
      setReparsing(false);
    }
  }

  // Fire-and-forget: the Dashboard tab is a nice-to-have to restore, not
  // worth blocking the UI or surfacing errors over.
  /** Re-runs the stored pandas script on the filtered subset and swaps the
   * whole dashboard at once — panels can never end up filtered differently
   * from each other because they all come from one execution. */
  async function applyDashboardFilter(nextRange: string, nextDims: Record<string, string[]>) {
    if (!dashFilters?.can_filter) return;
    setRefiltering(true);
    try {
      const res = await api.refilterDashboard({
        time_column: dashFilters.filters.time_column,
        time_range: nextRange,
        dimensions: nextDims,
      });
      const items: DashboardItem[] = [
        ...res.kpis.map((k, i) => ({
          id: `db-kpi-f-${Date.now()}-${i}`,
          type: "kpi" as const,
          title: k.name,
          scalar: k.value,
          compareValue: k.compare_value,
          compareLabel: k.compare_label,
        })),
        ...res.charts.map((c, i) => ({
          id: `db-chart-f-${Date.now()}-${i}`,
          type: "chart" as const,
          title: c.title,
          chart: layoutChartToSpec(c),
          role: c.role,
          size: c.size,
        })),
      ];
      setDashboardItems(items);
      persistDashboardItems(items);
      setFilteredRows(res.rows);
    } catch (e) {
      setMessages((m) => [...m, { role: "assistant", text: `Lỗi lọc dashboard: ${(e as Error).message}` }]);
    } finally {
      setRefiltering(false);
    }
  }

  function persistDashboardItems(items: DashboardItem[]) {
    api.saveDashboardItems(items).catch((err) => console.error("Failed to save dashboard items:", err));
  }

  // --- Add KPI or Chart to the permanent Dashboard sheet tab ---
  function addToDashboard(type: "kpi" | "chart", title: string, payload: any) {
    const id = `db-${Date.now()}`;
    const newItem: DashboardItem = {
      id,
      type,
      title,
      scalar: type === "kpi" ? payload : undefined,
      chart: type === "chart" ? payload : undefined,
    };
    setDashboardItems((prev) => {
      const next = [...prev, newItem];
      persistDashboardItems(next);
      return next;
    });
    setActiveKey("dashboard");
    setDisplayChart(null);
    setDisplayScalar(null);
  }

  function deleteDashboardItem(id: string) {
    setDashboardItems((prev) => {
      const next = prev.filter((item) => item.id !== id);
      persistDashboardItems(next);
      return next;
    });
  }

// "cho" was dropped from the action-verb list — it's one of the most common
// words in Vietnamese ("cho tôi biết...", "cho là...") and made almost any
// sentence containing "dashboard"/"báo cáo" misfire into auto-build.
const _BUILD_VERBS = ["tạo", "build", "dựng", "lập", "làm", "generate", "create", "make", "setup", "thiết lập", "vẽ", "viết"];

function isDashboardBuildRequest(query: string): boolean {
  const q = query.toLowerCase();
  const hasDashboardWord = q.includes("dashboard") || q.includes("bảng điều khiển");
  return hasDashboardWord && _BUILD_VERBS.some((v) => q.includes(v));
}

// Distinct from dashboard-build: "báo cáo" alone used to fall into the same
// bucket as "dashboard" and got silently turned into a dashboard-build
// request instead of the actual executive-report feature ("📄 Tạo báo cáo
// cho sếp"). Require language that specifically asks for that report.
//
// The two checks are NOT naturally mutually exclusive — a perfectly normal
// phrase like "Tạo dashboard báo cáo doanh thu cho sếp" contains both a
// dashboard word AND a boss-context report word. Dashboard-build intent wins
// that overlap: bail out here whenever a dashboard word is present, so
// isDashboardBuildRequest gets first claim on it instead of this function
// wrongly blocking the (usually empty-dashboard) report path.
function isExecutiveReportRequest(query: string): boolean {
  const q = query.toLowerCase();
  if (q.includes("dashboard") || q.includes("bảng điều khiển")) return false;
  const mentionsReport = q.includes("báo cáo") || q.includes("report");
  if (!mentionsReport) return false;
  const bossContext = ["sếp", "cấp trên", "điều hành", "executive", "quản lý", "boss"];
  return _BUILD_VERBS.some((v) => q.includes(v)) && bossContext.some((b) => q.includes(b));
}

  async function send(text?: string) {
    const q = (text ?? input).trim();
    if (!q || busy) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", text: q }]);

    if (isExecutiveReportRequest(q)) {
      if (dashboardItems.length === 0) {
        setMessages((m) => [...m, {
          role: "assistant",
          text: "Chưa có dashboard nào để viết báo cáo. Bạn bấm \"🤖 Dashboard tự động\" hoặc ghim vài KPI/biểu đồ từ câu trả lời trước, rồi mình sẽ viết báo cáo cho sếp.",
        }]);
        return;
      }
      handleGenerateReport();
      return;
    }

    if (isDashboardBuildRequest(q)) {
      buildDashboardAuto(q);
      return;
    }

    setBusy(true);
    setChatStage("");
    setChatReason("");
    setChatThoughts([]);
    const startedAt = Date.now();

    const history: ChatTurn[] = messages
      .map((m): ChatTurn | null => {
        if (m.role === "user") return { role: "user", content: m.text ?? "" };
        if (!m.reply) return null;
        if (m.reply.error) return { role: "assistant", content: m.reply.error, kind: "error" };
        if (!m.reply.answer) return null;
        const kind = m.reply.table ? "table" : m.reply.chart ? "chart" : m.reply.scalar != null ? "scalar" : "text";
        return { role: "assistant", content: m.reply.answer, kind };
      })
      .filter((x): x is ChatTurn => x !== null);

    const isInitialEmpty = tables.length === 0;

    try {
      let reply: ChatReply | null = null;
      const thoughts: string[] = [];
      await api.chat(q, history, (e) => {
        if (e.type === "step") {
          // The pool prefixes live thought summaries with 💭; they belong in
          // the collapsible transcript, not in the one-line stage indicator.
          if (e.message.startsWith("💭")) {
            const t = e.message.replace(/^💭\s*/, "");
            thoughts.push(t);
            setChatThoughts((prev) => [...prev, t]);
          } else {
            setChatStage(e.message);
          }
        } else if (e.type === "reason") {
          setChatReason(e.message);
        } else if (e.type === "error") {
          throw new Error(e.message);
        } else if (e.type === "done") {
          const { type: _t, ...rest } = e;
          reply = rest as ChatReply;
        }
      }, selectedSources);

      if (!reply) throw new Error("Không nhận được câu trả lời từ máy chủ.");
      const finalReply: ChatReply = reply;
      const thinkSecs = Math.round((Date.now() - startedAt) / 100) / 10;

      let currentTables = tables;
      if (isInitialEmpty || finalReply.generated) {
        try {
          const res = await api.tables();
          if (res.tables && res.tables.length > 0) {
            setTables(res.tables);
            currentTables = res.tables;
            // Newly synthesized sheets must join the selection or subsequent
            // questions would silently filter them out.
            setSelectedSources(res.tables.map((p) => p.source_id));
            const cache = { ...gridCache };
            for (const p of res.tables) {
              if (p.grid) cache[p.source_id] = p.grid;
            }
            setGridCache(cache);
          }
        } catch (err) {
          console.error("Failed to sync tables after data generation:", err);
        }
      }

      let resultKey: string | undefined;
      if (finalReply.table) {
        resultKey = `result:${Date.now()}`;
        const key = resultKey;
        setResultSheets((prev) => [{ key, name: `Kết quả ${prev.length + 1}`, table: finalReply.table! }, ...prev]);
        setActiveKey(key);
      } else if (activeKey === "" && currentTables.length > 0) {
        const firstActive = currentTables.find((p) => p.grid)?.source_id || currentTables[0].source_id;
        setActiveKey(firstActive);
      }

      // Chart/KPI → display on sheet area (full width)
      if (finalReply.chart) setDisplayChart(finalReply.chart);
      if (finalReply.scalar != null && !finalReply.table) {
        setDisplayScalar(finalReply.scalar);
        setDisplayScalarLabel(q);
      }

      setMessages((m) => [...m, {
        role: "assistant", reply: finalReply, resultKey,
        thoughts: thoughts.length ? thoughts : undefined,
        thinkSecs,
      }]);
    } catch (e) {
      setMessages((m) => [...m, { role: "assistant", text: `Lỗi: ${(e as Error).message}` }]);
    } finally {
      setBusy(false);
    }
  }

  const hasData = tables.length > 0;

  const activeSheet: GridSheet | null = useMemo(() => {
    if (activeKey === "dashboard") return null;
    const rs = resultSheets.find((r) => r.key === activeKey);
    if (rs) return { name: rs.name, grid: tableToGrid(rs.table) };
    const t = tables.find((p) => p.source_id === activeKey);
    if (t && gridCache[t.source_id]) return { name: t.sheet, grid: gridCache[t.source_id] };
    return null;
  }, [activeKey, resultSheets, tables, gridCache]);

  // Shared card renderer for every dashboard layout below - only the
  // surrounding arrangement (grid/columns/story flow) differs per layout.
  function renderDashboardCard(item: DashboardItem, extraClass?: string) {
    return (
      <div key={item.id} className={`dashboard-card${extraClass ? ` ${extraClass}` : ""}`}>
        <button className="dashboard-card__delete" onClick={() => deleteDashboardItem(item.id)}>✕</button>
        <div className="dashboard-card__content">
          <h3 className="dashboard-card__title">{item.title}</h3>
          {item.type === "kpi" && (
            <div className="dashboard-card__kpi">
              <span className="dashboard-card__kpi-value">{fmtCell(item.scalar!)}</span>
              {(() => {
                // A number only becomes information next to a reference point.
                const cur = typeof item.scalar === "number" ? item.scalar : Number(item.scalar);
                const prev = item.compareValue;
                if (prev == null || !isFinite(cur) || !isFinite(prev) || prev === 0) return null;
                const pct = ((cur - prev) / Math.abs(prev)) * 100;
                const up = pct >= 0;
                return (
                  <span className="dashboard-card__kpi-delta" style={{ color: up ? "#15803d" : "#b91c1c" }}>
                    {up ? "▲" : "▼"} {Math.abs(pct).toFixed(1)}%
                    <span className="dashboard-card__kpi-delta-label">
                      {" "}so với {item.compareLabel || "kỳ trước"}
                    </span>
                  </span>
                );
              })()}
            </div>
          )}
          {item.type === "chart" && item.chart && (
            <div className="dashboard-card__chart">
              <MiniChart spec={item.chart} showTitle={false} />
            </div>
          )}
        </div>
      </div>
    );
  }

  function renderDashboardBody(items: DashboardItem[], layout: DashboardLayout) {
    const kpis = items.filter((i) => i.type === "kpi");
    const charts = items.filter((i) => i.type === "chart");

    if (layout === "kpi-first") {
      return (
        <>
          {kpis.length > 0 && <div className="dashboard-row dashboard-row--kpi">{kpis.map((i) => renderDashboardCard(i))}</div>}
          <div className="dashboard-grid">{charts.map((i) => renderDashboardCard(i))}</div>
        </>
      );
    }
    if (layout === "two-column") {
      // Narrow detail rail beside a wide overview, rather than an arbitrary
      // alternating split: rankings and granular views read as a list on the
      // side, while headline numbers and the main charts own the main area.
      let side = charts.filter((i) => i.role === "breakdown" || i.role === "detail");
      let main = charts.filter((i) => !side.includes(i));
      if (side.length === 0 && charts.length > 2) {
        // Legacy/hand-pinned charts carry no role. Splitting anyway beats
        // rendering an empty rail next to everything crammed in one column.
        const cut = Math.ceil(charts.length * 0.65);
        main = charts.slice(0, cut);
        side = charts.slice(cut);
      }
      return (
        <div className="dashboard-two-col">
          <div className="dashboard-two-col__side">
            {side.length > 0 && <div className="dashboard-zone__label">Chi tiết</div>}
            {side.map((i) => renderDashboardCard(i))}
          </div>
          <div className="dashboard-two-col__main">
            {kpis.length > 0 && (
              <div className="dashboard-row dashboard-row--kpi">{kpis.map((i) => renderDashboardCard(i))}</div>
            )}
            <div className="dashboard-two-col__main-grid">{main.map((i) => renderDashboardCard(i))}</div>
          </div>
        </div>
      );
    }
    if (layout === "storytelling") {
      return <div className="dashboard-story">{items.map((i) => renderDashboardCard(i, "dashboard-card--story"))}</div>;
    }
    if (layout === "overview-detail") {
      // In the "Overview -> Detail" reference design, it is a unified dashboard
      // Top row: KPIs (compact)
      // Second row: Trends + Analysis (mixed span)
      // Third row: Detail Data tables
      const zone = (i: DashboardItem): ChartRole => i.role ?? "analysis";
      const trend = charts.filter((i) => zone(i) === "trend");
      const analysis = charts.filter((i) => zone(i) === "analysis");
      const breakdown = charts.filter((i) => zone(i) === "breakdown");
      const detail = charts.filter((i) => zone(i) === "detail");

      // Grid is 3 columns wide.
      // lg -> spans 2 cols, sm -> spans 1 col, md -> spans 1 col.
      const spanOf = (i: DashboardItem) =>
        i.size === "lg" ? "dashboard-card--span2"
          : "dashboard-card--span1";

      return (
        <div className="dashboard-overview-layout">
          {kpis.length > 0 && (
            <div className="dashboard-row dashboard-row--kpi">
              {kpis.map((i) => renderDashboardCard(i, "dashboard-card--kpi-compact"))}
            </div>
          )}
          <div className="dashboard-overview-grid">
            {[...trend, ...analysis, ...breakdown, ...detail].map((i) => renderDashboardCard(i, spanOf(i)))}
          </div>
        </div>
      );
    }
    // "grid" (default) — uniform Grid Card layout, KPIs and charts side by side.
    return <div className="dashboard-grid">{items.map((i) => renderDashboardCard(i))}</div>;
  }

  return (
    <>
      <header className="topbar">
        <div className="topbar__left">
          <Logo />
          <h1 className="topbar__brand-title">GraphSheet</h1>
          <span className="topbar__tag">AI lập kế hoạch · Python tính toán</span>
        </div>
        <div className="topbar__tabs">
          <button
            className={`topbar-tab${sidebarTab === "sources" ? " topbar-tab--active" : ""}`}
            onClick={() => setSidebarTab("sources")}
          >
            📁 Tài liệu ({tables.length})
          </button>
          <button
            className={`topbar-tab${sidebarTab === "memory" ? " topbar-tab--active" : ""}`}
            data-tour="memory-tab"
            onClick={() => {
              setSidebarTab("memory");
              fetchMemoryDiagnostics();
            }}
          >
            🧠 Ký ức & Kỹ năng
          </button>
        </div>
        <button
          className="topbar-help"
          title="Xem lại hướng dẫn sử dụng"
          onClick={startTour}
        >
          ?
        </button>
        <AuthBar email={email} onLogout={onLogout} />
      </header>
      {showTour && (
        <Tour
          steps={tourSteps}
          onFinish={finishTour}
        />
      )}
      {sidebarTab === "memory" ? (
      <div className="memory-fullpage">
        <div className="memory-fullpage__graph">
          <div className="sidebar-section-title">Mạng lưới ký ức (Neo4j)</div>
          {loadingMemory ? (
            <div style={{ color: "#8b949e", fontSize: "0.85rem", textAlign: "center", marginTop: "2rem" }}>Đang tải ký ức...</div>
          ) : memoryData?.enabled ? (
            <MemoryGraphCanvas nodes={memoryData.nodes} edges={memoryData.edges} />
          ) : (
            <div style={{ color: "#8b949e", fontSize: "0.85rem", textAlign: "center", marginTop: "2rem" }}>
              Chưa thể kết nối đến cơ sở dữ liệu Ký ức (Neo4j).
            </div>
          )}
        </div>
        <div className="memory-fullpage__skills">
          <div className="sidebar-section-title" style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <span>Ghi nhớ về bạn ({memoryData?.behaviors?.length ?? 0})</span>
            {(memoryData?.behaviors?.length ?? 0) > 0 && (
              <button className="button button--small button--secondary" onClick={handleForgetBehaviors}>
                🗑 Quên hết
              </button>
            )}
          </div>
          {(memoryData?.behaviors?.length ?? 0) === 0 ? (
            <div style={{ color: "#8b949e", fontSize: "0.82rem", marginBottom: "1rem" }}>
              Chưa có ghi nhớ nào. AI sẽ tự chắt lọc thói quen của bạn sau mỗi phiên làm việc (khi bạn nghỉ ~30 phút).
            </div>
          ) : (
            <div className="behaviors-list">
              {(memoryData?.behaviors ?? []).map((b: any) => (
                <div key={b.id} className="behavior-card">
                  <span className={`behavior-category behavior-category--${b.category}`}>
                    {b.category === "habit" ? "Thói quen" : b.category === "preference" ? "Sở thích" : "Việc dở dang"}
                  </span>
                  <span className="behavior-desc">{b.description}</span>
                  {(b.usage_count ?? 0) > 0 && (
                    <span className="behavior-usage">dùng {b.usage_count} lần</span>
                  )}
                </div>
              ))}
            </div>
          )}

          <div className="sidebar-section-title" style={{ marginTop: "1rem" }}>Kỹ năng tác vụ ({memoryData?.skills.length ?? 0})</div>
          <div className="skills-list skills-list--grid">
            {(memoryData?.skills ?? []).map((skill) => (
              <div key={skill.name} className="skill-card">
                <div className="skill-header">
                  <span className="skill-name">{skill.name}</span>
                  <span className={`skill-status-badge skill-status--${skill.status}`}>
                    {skill.status === "active" ? "Hoạt động" : "Đã tắt"}
                  </span>
                </div>
                <p className="skill-desc">{skill.description}</p>
                <div className="skill-meta">
                  <span>Sử dụng: {skill.usage_count}</span>
                  <span>Tỉ lệ thành công:{" "}
                    <span className={`skill-success-rate ${
                      skill.usage_count === 0 ? "" : (skill.success_count / skill.usage_count) >= 0.7 ? "good" : "bad"
                    }`}>
                      {skill.usage_count === 0 ? "100%" : `${Math.round((skill.success_count / skill.usage_count) * 100)}%`}
                    </span>
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
      ) : (
      <div className="chat-workspace" style={{ gridTemplateColumns: `280px 1fr 6px ${chatWidth}px`, overflow: "hidden" }}>
      {/* ── Left Sidebar (NotebookLM style) ── */}
      <div className="left-sidebar">
        <div className="sidebar-content">
          <div className="sidebar-section-title">Nguồn dữ liệu hiện tại</div>
          {tables.length === 0 ? (
            <div style={{ color: "#8b949e", fontSize: "0.85rem", textAlign: "center", marginTop: "2rem" }}>
              Chưa tải tài liệu nào lên. Hãy tải file lên hoặc yêu cầu AI sinh dữ liệu để bắt đầu.
            </div>
          ) : (
            <>
              <div className="source-actions">
                <button onClick={handleSelectAllSources}>Chọn tất cả</button>
                <button onClick={handleDeselectAllSources}>Bỏ chọn tất cả</button>
              </div>
              {/* Group sheets by filename */}
              {Object.entries(
                tables.reduce((acc, t) => {
                  if (!acc[t.filename]) acc[t.filename] = [];
                  acc[t.filename].push(t);
                  return acc;
                }, {} as Record<string, FileProfile[]>)
              ).map(([filename, sheets]) => (
                <div key={filename} className="source-file-group">
                  <div className="source-file-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={filename}>📁 {filename}</span>
                    <button 
                      className="button button--small button--secondary"
                      onClick={() => handleDeleteFile(filename)}
                      title="Xóa file này"
                      style={{ padding: "2px 6px", fontSize: "1rem", minWidth: "auto", background: "transparent", border: "none", cursor: "pointer", opacity: 0.7 }}
                    >
                      🗑️
                    </button>
                  </div>
                  <div className="source-sheet-list">
                    {sheets.map((sheet) => {
                      const isChecked = selectedSources.includes(sheet.source_id);
                      return (
                        <label key={sheet.source_id} className="source-sheet-item">
                          <input
                            type="checkbox"
                            className="source-sheet-checkbox"
                            checked={isChecked}
                            onChange={() => handleToggleSource(sheet.source_id)}
                          />
                          <span className="source-sheet-name" title={sheet.sheet}>
                            {sheet.sheet}
                          </span>
                          <span className="source-sheet-count">
                            {sheet.grid_rows || sheet.row_count || 0} dòng
                          </span>
                        </label>
                      );
                    })}
                  </div>
                </div>
              ))}
            </>
          )}
        </div>
      </div>

      <div className="chat-grid">
        {uploading ? (
          <SkeletonGrid
            head={
              <WorkBench
                stage={workStage}
                message={statusText}
                thoughts={workThoughts}
                engine={workEngine}
              />
            }
          />
        ) : hasData ? (
          <>
            <div className="sheet-tabs" data-tour="sheet-tabs">
              {/* Dashboard tab appears first if there are pinned items */}
              {dashboardItems.length > 0 && (
                <button
                  key="dashboard"
                  data-tour="dashboard-tab"
                  className={`sheet-tab sheet-tab--dashboard${activeKey === "dashboard" ? " sheet-tab--dashboard-active" : ""}`}
                  onClick={() => setActiveKey("dashboard")}
                >
                  📊 Dashboard ({dashboardItems.length})
                </button>
              )}
              {resultSheets.map((r) => (
                <button
                  key={r.key}
                  className={`sheet-tab sheet-tab--result${activeKey === r.key ? " sheet-tab--active" : ""}`}
                  onClick={() => setActiveKey(r.key)}
                >
                  📊 {r.name}
                </button>
              ))}
              {tables.map((t) => (
                <button
                  key={t.source_id}
                  className={`sheet-tab${activeKey === t.source_id ? " sheet-tab--active" : ""}${t.has_data ? "" : " sheet-tab--empty"}`}
                  onClick={() => selectSheet(t.source_id)}
                  title={`${t.grid_rows ?? 0} dòng`}
                >
                  {t.sheet}
                </button>
              ))}
            </div>
            <div className="sheet-body" style={{ display: "flex", flexDirection: "column" }}>
              {activeKey !== "dashboard" && activeSheet && (
                <div className="sheet-body-toggle-bar" style={{ display: "flex", justifyContent: "flex-end", padding: "0.5rem 1rem", borderBottom: "1px solid #e1e7e4", gap: "8px", background: "#fcfdfe" }}>
                  <button 
                    className={`button button--small ${viewMode === "grid" ? "button--primary" : "button--secondary"}`} 
                    onClick={() => setViewMode("grid")}
                  >
                    🧮 Xem dạng bảng
                  </button>
                  <button
                    className={`button button--small ${viewMode === "bi" ? "button--primary" : "button--secondary"}`}
                    onClick={() => setViewMode("bi")}
                  >
                    📊 Kéo thả biểu đồ (BI)
                  </button>
                </div>
              )}

              {/* Header-detection banner: show what row the AI treated as the
                  header, and let the user correct it when the file is messy. */}
              {(() => {
                const t = tables.find((p) => p.source_id === activeKey);
                const det = t?.detection;
                if (!t || !det) return null;
                const row1 = (det.header_row ?? 0) + 1;
                const uncertain = det.low_confidence && !det.manual;
                return (
                  <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap", padding: "0.4rem 1rem", fontSize: "0.82rem", background: uncertain ? "#fff7ed" : "#f3f9f6", borderBottom: "1px solid #e1e7e4", color: "#3d4d45" }}>
                    <span>
                      {uncertain ? "⚠️" : "🧠"} Tiêu đề nhận diện ở <strong>dòng {row1}</strong>
                      {det.two_level_header ? ` + dòng ${row1 + 1} (tiêu đề 2 tầng, đã ghép tên cột)` : ""}
                      {det.manual ? " (bạn đã chọn)" : ""}
                      {det.totals_dropped > 0 ? ` · đã bỏ ${det.totals_dropped} dòng tổng` : ""}
                      {uncertain ? " · AI chưa chắc chắn" : ""}
                    </span>
                    {!editingHeader ? (
                      <button className="button button--small button--secondary" onClick={() => { setEditingHeader(true); setHeaderRowInput(String(row1)); }}>
                        ✏️ Sửa dòng tiêu đề
                      </button>
                    ) : (
                      <span style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                        Dòng tiêu đề:
                        <input
                          type="number"
                          min={1}
                          value={headerRowInput}
                          onChange={(e) => setHeaderRowInput(e.target.value)}
                          style={{ width: "64px", padding: "2px 6px", borderRadius: "6px", border: "1px solid #cbd5d0" }}
                        />
                        <button className="button button--small button--primary" disabled={reparsing} onClick={() => handleReparse(t.source_id, parseInt(headerRowInput, 10))}>
                          {reparsing ? "..." : "Áp dụng"}
                        </button>
                        <button className="button button--small button--secondary" disabled={reparsing} onClick={() => setEditingHeader(false)}>
                          Hủy
                        </button>
                      </span>
                    )}
                  </div>
                );
              })()}
              {(() => {
                // What the data means + deterministic quality warnings. The
                // profiler has always computed `flags`; until now they were
                // shipped to the browser and never rendered anywhere.
                const t = tables.find((p) => p.source_id === activeKey);
                const sem = t?.semantics;
                const flags = t?.flags ?? [];
                if (!t || (!sem && flags.length === 0)) return null;
                return (
                  <div style={{ padding: "0.45rem 1rem", fontSize: "0.8rem", background: "#f7faf9", borderBottom: "1px solid #e1e7e4", color: "#3d4d45", display: "flex", flexDirection: "column", gap: "3px" }}>
                    {sem && (
                      <span>
                        🔬 <strong>{sem.grain_description || sem.grain_type}</strong>
                        {sem.domain ? ` · ${sem.domain}` : ""}
                        {sem.sheet_role === "fact" ? " · bảng giao dịch" : sem.sheet_role === "dimension" ? " · bảng danh mục" : ""}
                        {sem.primary_measure ? ` · chỉ số chính: ${sem.primary_measure}${sem.measure_unit ? ` (${sem.measure_unit})` : ""}` : ""}
                      </span>
                    )}
                    {(sem?.caveats ?? []).map((c, i) => (
                      <span key={`c${i}`} style={{ color: "#8a5a1b" }}>⚠ {c}</span>
                    ))}
                    {flags.map((f, i) => (
                      <span key={`f${i}`} style={{ color: "#8a5a1b" }}>⚠ {f}</span>
                    ))}
                  </div>
                );
              })()}
              {activeKey === "dashboard" ? (
                <div className="dashboard-view animate-fade-in" data-palette={dashboardPalette} style={{ flex: 1, overflowY: "auto" }}>
                  <div className="dashboard-unified-header">
                    <div className="dashboard-grip">
                      <h2 className="dashboard-grip__title">Operations Dashboard</h2>
                      
                      {/* Tích hợp trực tiếp Filters vào trong Grip */}
                      <div className="dashboard-grip__filters">
                        {dashFilters?.can_filter ? (
                          <>
                            {dashFilters.filters.time_ranges.length > 0 ? (
                              <select
                                className="dashboard-grip__select"
                                value={timeRange}
                                disabled={refiltering}
                                onChange={(e) => {
                                  setTimeRange(e.target.value);
                                  applyDashboardFilter(e.target.value, dimFilters);
                                }}
                              >
                                <option value="all">Thời gian (Tất cả)</option>
                                {dashFilters.filters.time_ranges.map((r) => (
                                  <option key={r.key} value={r.key}>{r.label}</option>
                                ))}
                              </select>
                            ) : (
                              <select className="dashboard-grip__select" disabled title="Không có cột thời gian">
                                <option>Thời gian (N/A)</option>
                              </select>
                            )}

                            {dashFilters.filters.dimensions.map((d) => (
                              <select
                                key={d.column}
                                className="dashboard-grip__select"
                                value={dimFilters[d.column]?.[0] ?? ""}
                                disabled={refiltering}
                                onChange={(e) => {
                                  const next = { ...dimFilters };
                                  if (e.target.value) next[d.column] = [e.target.value];
                                  else delete next[d.column];
                                  setDimFilters(next);
                                  applyDashboardFilter(timeRange, next);
                                }}
                              >
                                <option value="">{d.column} (Tất cả)</option>
                                {d.options.map((o) => (
                                  <option key={o} value={o}>{o}</option>
                                ))}
                              </select>
                            ))}

                            {(timeRange !== "all" || Object.keys(dimFilters).length > 0) && !refiltering && (
                              <button
                                className="dashboard-grip__clear"
                                onClick={() => {
                                  setTimeRange("all");
                                  setDimFilters({});
                                  applyDashboardFilter("all", {});
                                }}
                              >
                                ✕ Xóa lọc
                              </button>
                            )}
                            
                            {refiltering ? (
                              <span style={{fontSize: "0.75rem", color: "#6b7280", marginLeft: "4px"}}>⏳ Đang tính...</span>
                            ) : filteredRows != null ? (
                              <span style={{fontSize: "0.75rem", color: "#6b7280", marginLeft: "4px"}}>({filteredRows.toLocaleString("vi-VN")} dòng)</span>
                            ) : null}
                          </>
                        ) : (
                          /* Hiển thị các nút mờ nếu dashboard hiện tại không hỗ trợ filter */
                          <>
                            <select className="dashboard-grip__select" disabled style={{ opacity: 0.5 }}>
                              <option>Thời gian (N/A)</option>
                            </select>
                            <select className="dashboard-grip__select" disabled style={{ opacity: 0.5 }}>
                              <option>Khu vực (N/A)</option>
                            </select>
                            <select className="dashboard-grip__select" disabled style={{ opacity: 0.5 }}>
                              <option>Phân loại (N/A)</option>
                            </select>
                          </>
                        )}
                      </div>
                    </div>

                    <div className="dashboard-actions">
                      <select
                        className="dashboard-select"
                        value={dashboardLayout}
                        onChange={(e) => setDashboardLayout(e.target.value as DashboardLayout)}
                        title="Cách sắp xếp dashboard"
                      >
                        <option value="grid">🔲 Grid Card</option>
                        <option value="kpi-first">📌 KPI First</option>
                        <option value="two-column">▥ Hai cột</option>
                        <option value="storytelling">📖 Storytelling</option>
                        <option value="overview-detail">🔍 Tổng quan → Chi tiết</option>
                      </select>
                      <select
                        className="dashboard-select"
                        value={dashboardPalette}
                        onChange={(e) => setDashboardPalette(e.target.value as DashboardPalette)}
                        title="Bảng màu dashboard"
                      >
                        <option value="emerald">🟢 Emerald</option>
                        <option value="ocean">🔵 Ocean</option>
                        <option value="sunset">🟠 Sunset</option>
                      </select>
                      <button
                        className="button button--small button--secondary"
                        disabled={generatingReport || dashboardItems.length === 0}
                        onClick={handleGenerateReport}
                      >
                        {generatingReport ? "Đang viết báo cáo..." : "📄 Phân tích"}
                      </button>
                      <button
                        className="button button--export"
                        disabled={exporting || dashboardItems.length === 0}
                        onClick={handleExportExcel}
                      >
                        {exporting ? "Đang tạo..." : "📥 Tải Excel"}
                      </button>
                    </div>
                  </div>

                  {/* Same guard, same block, as the chat path — placed before
                      the tiles so it is read before the numbers it qualifies. */}
                  {dashboardJoinWarnings.length > 0 && (
                    <div className="join-warn">
                      <div className="join-warn__head">⚠️ Cẩn thận khi cộng các số dưới đây</div>
                      <ul className="join-warn__list">
                        {dashboardJoinWarnings.map((w, k) => (
                          <li key={k}>{w}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {/* Session report artifacts */}
                  {/* Report history button moved below or kept in actions */}

                  {/* Session report artifacts: every generated report is kept so
                      it can be reopened or exported to Word later. */}
                  {showSavedReports && (
                    <div style={{ margin: "0.75rem 0", padding: "0.75rem 1rem", background: "#fff", borderRadius: "10px", border: "1px solid #e1e7e4" }}>
                      <strong style={{ display: "block", marginBottom: "0.5rem", fontSize: "0.9rem" }}>🗂 Báo cáo đã lưu trong phiên</strong>
                      {savedReports.length === 0 ? (
                        <p style={{ fontSize: "0.85rem", color: "#5c6b63", margin: 0 }}>
                          Chưa có báo cáo nào. Bấm "📄 Tạo báo cáo cho sếp" để tạo - báo cáo sẽ tự được lưu lại ở đây.
                        </p>
                      ) : (
                        savedReports.map((sr) => (
                          <div key={sr.id} style={{ display: "flex", alignItems: "center", gap: "8px", padding: "6px 0", borderBottom: "1px solid #eef2ef" }}>
                            <span style={{ flex: 1, fontSize: "0.88rem", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                              📄 {sr.title}
                            </span>
                            <span style={{ fontSize: "0.75rem", color: "#8b949e", flexShrink: 0 }}>
                              {new Date(sr.created_at * 1000).toLocaleString("vi-VN", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}
                            </span>
                            <button className="button button--small button--secondary" onClick={() => { setReport({ ...sr.report, id: sr.id }); setShowSavedReports(false); }}>
                              Xem
                            </button>
                            <button className="button button--small button--secondary" onClick={() => handleDownloadReport(sr.id)}>
                              📥 Word
                            </button>
                          </div>
                        ))
                      )}
                    </div>
                  )}

                  {/* Grounded insight write-up (Phase 1) — real numbers already
                      computed by pandas, cited by the LLM, not invented. */}
                  {dashboardInsights.length > 0 && (
                    <div className="dashboard-insights" style={{ margin: "0.75rem 0", padding: "0.9rem 1.1rem", borderRadius: "10px" }}>
                      <strong style={{ display: "block", marginBottom: "0.4rem" }}>📝 Nhận xét</strong>
                      {dashboardInsights.map((t, i) => (
                        <p key={i} style={{ margin: "0.35rem 0", fontSize: "0.92rem", lineHeight: 1.5 }}>{t}</p>
                      ))}
                    </div>
                  )}

                  {/* Executive report-to-boss (Phase 6) */}
                  {report && (
                    <div className="dashboard-report" style={{ margin: "0.75rem 0", padding: "1rem 1.2rem", background: "#fff", borderRadius: "10px", border: "1px solid #e1e7e4", position: "relative" }}>
                      <button className="result-card__close" onClick={() => setReport(null)} style={{ position: "absolute", top: "0.5rem", right: "0.5rem" }}>✕</button>
                      <strong style={{ display: "block", marginBottom: "0.4rem" }}>
                        📄 Báo cáo điều hành
                        {report.id && (
                          <button
                            className="button button--small button--secondary"
                            style={{ marginLeft: "10px" }}
                            onClick={() => handleDownloadReport(report.id!)}
                          >
                            📥 Tải Word (.docx)
                          </button>
                        )}
                      </strong>
                      <p style={{ fontStyle: "italic", margin: "0.4rem 0" }}>{report.executive_summary}</p>
                      {report.key_findings.length > 0 && (
                        <>
                          <strong style={{ fontSize: "0.85rem" }}>Phát hiện chính</strong>
                          <ul style={{ margin: "0.3rem 0 0.6rem", paddingLeft: "1.2rem" }}>
                            {report.key_findings.map((f, i) => <li key={i} style={{ fontSize: "0.9rem", marginBottom: "0.2rem" }}>{f}</li>)}
                          </ul>
                        </>
                      )}
                      {report.anomalies.length > 0 && (
                        <>
                          <strong style={{ fontSize: "0.85rem" }}>Bất thường</strong>
                          <ul style={{ margin: "0.3rem 0 0.6rem", paddingLeft: "1.2rem" }}>
                            {report.anomalies.map((a, i) => <li key={i} style={{ fontSize: "0.9rem", marginBottom: "0.2rem" }}>{a}</li>)}
                          </ul>
                        </>
                      )}
                      {report.recommendations.length > 0 && (
                        <>
                          <strong style={{ fontSize: "0.85rem" }}>Đề xuất</strong>
                          <ul style={{ margin: "0.3rem 0 0", paddingLeft: "1.2rem" }}>
                            {report.recommendations.map((r, i) => <li key={i} style={{ fontSize: "0.9rem", marginBottom: "0.2rem" }}>{r}</li>)}
                          </ul>
                        </>
                      )}
                    </div>
                  )}

                  {renderDashboardBody(dashboardItems, dashboardLayout)}
                </div>
              ) : loadingSheet && !activeSheet ? (
                <div className="chat-empty" style={{ flex: 1 }}>
                  <p>Đang nạp sheet…</p>
                </div>
              ) : viewMode === "bi" && activeSheet ? (
                <div style={{ flex: 1, overflow: "hidden" }}>
                  <ViewErrorBoundary>
                    <BIExplore grid={activeSheet.grid} />
                  </ViewErrorBoundary>
                </div>
              ) : (
                <div style={{ flex: 1, display: "flex", flexDirection: "column" }}>
                  <ViewErrorBoundary>
                    <UniverGrid sheet={activeSheet} />
                  </ViewErrorBoundary>
                </div>
              )}



              {/* ── Chart/KPI display panel (shown after reply, closeable) ── */}
              {(displayChart || displayScalar != null) && (
                <div className="result-overlay">
                  <div className="result-card">
                    <button className="result-card__close" onClick={() => { setDisplayChart(null); setDisplayScalar(null); }}>✕</button>
                    {displayScalar != null && (
                      <div className="result-card__kpi">
                        <div className="result-card__kpi-label">{displayScalarLabel}</div>
                        <div className="result-card__kpi-value">{fmtCell(displayScalar)}</div>
                        <button
                          className="button button--small button--kpi-action"
                          onClick={() => addToDashboard("kpi", displayScalarLabel, displayScalar)}
                        >
                          📌 Ghi vào Dashboard
                        </button>
                      </div>
                    )}
                    {displayChart && (
                      <div className="result-card__chart">
                        <MiniChart spec={displayChart} />
                        <button
                          className="button button--small button--chart-action"
                          onClick={() => addToDashboard("chart", displayChart.title, displayChart)}
                        >
                          📌 Ghi vào Dashboard
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          </>
        ) : (
          <div className="chat-empty-premium animate-fade-in">
            <div className="empty-state-card">
              <div className="empty-state-icon">📊</div>
              <h2>Chào mừng bạn đến với AI Data Analyst</h2>
              <p className="empty-state-subtitle">Bạn có thể bắt đầu bằng một trong hai cách dưới đây:</p>
              
              <div className="empty-options-grid">
                <div className="empty-option-card">
                  <div className="option-number">Cách 1</div>
                  <h3>Tải file dữ liệu lên</h3>
                  <p>Chọn các file Excel (.xlsx, .xls) hoặc CSV để AI làm sạch và phân tích.</p>
                  <button className="button" onClick={() => fileRef.current?.click()}>
                    📁 Chọn file từ máy tính
                  </button>
                </div>
                
                <div className="empty-option-card">
                  <div className="option-number">Cách 2</div>
                  <h3>Sinh dữ liệu giả lập từ số 0</h3>
                  <p>Nhập yêu cầu vào khung chat bên cạnh để AI tự sinh dữ liệu và vẽ biểu đồ.</p>
                  <div className="suggested-scratch-list">
                    <button className="chip chip--suggest-scratch" disabled={busy} onClick={() => send("Tạo dữ liệu doanh số bán hàng quán cafe trong 3 tháng qua với 200 giao dịch")}>
                      ☕ Tạo dữ liệu quán Cafe
                    </button>
                    <button className="chip chip--suggest-scratch" disabled={busy} onClick={() => send("Tạo danh sách 100 nhân viên với các cột: Tên, Phòng ban, Mức lương, Ngày vào làm")}>
                      👥 Tạo danh sách Nhân sự
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
      <div className="chat-resizer" onMouseDown={startResize}></div>
      <div className="chat-panel">
        <div className="chat-panel__head">
          <strong>Hỏi đáp dữ liệu</strong>
          <div style={{ display: "flex", gap: "6px" }}>
            {hasData && (
              <button
                className="button button--small button--primary"
                data-tour="build-dashboard"
                onClick={() => buildDashboardAuto()}
                disabled={buildingDashboard || uploading}
                title="AI tự lên kế hoạch, tính toán và dựng cả dashboard (dùng nội dung ô nhập bên dưới làm yêu cầu, để trống = tổng quan)"
              >
                {buildingDashboard ? "🤖 Đang dựng..." : "🤖 Dashboard tự động"}
              </button>
            )}
            <button className="button button--small" data-tour="upload" onClick={() => fileRef.current?.click()} disabled={uploading}>
              {uploading ? "..." : hasData ? "+ Thêm file" : "Tải file"}
            </button>
          </div>
        </div>
        {buildingDashboard && (
          <div className="chat-panel__build-status" style={{ padding: "0.5rem 1rem", fontSize: "0.85rem", color: "#5c6b63", borderBottom: "1px solid #e1e7e4" }}>
            {buildStatus}
          </div>
        )}

        <div className="chat-log" ref={logRef}>
          {messages.map((m, i) => (
            <div key={i} className={`bubble bubble--${m.role}`}>
              {m.text && <MarkdownText text={m.text} />}

              {/* Upload-time concise suggestions in chat */}
              {m.suggestions && m.suggestions.length > 0 && (
                <div className="bubble__follow-up" style={{ marginTop: "0.6rem", display: "flex", flexWrap: "wrap", gap: "6px" }}>
                  {m.suggestions.slice(0, 3).map((q, j) => (
                    <button key={j} className="chip chip--suggest" disabled={busy} onClick={() => send(q)}>
                      💡 {q}
                    </button>
                  ))}
                </div>
              )}

              {m.reply && (
                <>
                  {/* Collapsed by default: proves the work happened without
                      turning every answer into a wall of reasoning text. */}
                  {m.thoughts && m.thoughts.length > 0 && (
                    <details className="thought-log">
                      <summary>
                        💭 Đã suy nghĩ {m.thinkSecs ? `${m.thinkSecs}s` : ""}
                        {m.thoughts.length > 1 ? ` · ${m.thoughts.length} bước` : ""}
                      </summary>
                      <div className="thought-log__body">
                        {m.thoughts.map((t, k) => (
                          <p key={k}>{t}</p>
                        ))}
                      </div>
                    </details>
                  )}

                  {m.reply.error ? (
                    <p className="bubble__error">⚠️ {m.reply.error}</p>
                  ) : m.reply.clarify ? (
                    // Asking beats guessing: a wrong assumption here produces a
                    // confidently wrong NUMBER, which is the one failure mode
                    // this product cannot afford.
                    <div className="clarify">
                      <div className="clarify__q">🤔 {m.reply.answer}</div>
                      <div className="clarify__opts">
                        {(m.reply.follow_up ?? []).slice(0, 3).map((o, j) => (
                          <button key={j} className="chip chip--clarify" disabled={busy} onClick={() => send(o)}>
                            {o}
                          </button>
                        ))}
                      </div>
                    </div>
                  ) : m.reply.answer ? (
                    <MarkdownText text={m.reply.answer} />
                  ) : null}

                  {/* A merge that inflates totals raises no error and looks
                      completely normal, so this sits ABOVE the answer: by the
                      time someone has read a wrong total, the warning has
                      already arrived too late. */}
                  {m.reply.join_warnings && m.reply.join_warnings.length > 0 && (
                    <div className="join-warn">
                      <div className="join-warn__head">⚠️ Cẩn thận khi cộng các số dưới đây</div>
                      <ul className="join-warn__list">
                        {m.reply.join_warnings.map((w, k) => (
                          <li key={k}>{w}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {/* The dig: shown as a reasoning chain, not a wall of prose,
                      so the user can follow how one finding led to the next. */}
                  {m.reply.investigation && m.reply.investigation.findings.length > 0 && (
                    <div className="investigation">
                      <div className="investigation__head">
                        🕵️ Đã đào sâu {m.reply.investigation.rounds ?? ""} bước
                      </div>
                      <ol className="investigation__list">
                        {m.reply.investigation.findings.map((f, k) => (
                          <li key={k}>{f}</li>
                        ))}
                      </ol>
                      {m.reply.investigation.conclusion && (
                        <div className="investigation__conclusion">
                          → {m.reply.investigation.conclusion}
                        </div>
                      )}
                    </div>
                  )}

                  {/* Scalar shown as inline badge in chat (full KPI is on sheet side) */}
                  {m.reply.scalar != null && !m.reply.table && (
                    <span className="bubble__scalar">{fmtCell(m.reply.scalar)}</span>
                  )}

                  {/* Mini table preview in chat + "Mở trong lưới" */}
                  {m.reply.table && (
                    <>
                      <div className="mini-table__wrap">
                        <table className="mini-table">
                          <thead>
                            <tr>
                              {m.reply.table.columns.map((c, j) => (
                                <th key={j}>{c}</th>
                              ))}
                            </tr>
                          </thead>
                          <tbody>
                            {m.reply.table.rows.slice(0, 6).map((r, j) => (
                              <tr key={j}>
                                {r.map((c, k) => (
                                  <td key={k}>{fmtCell(c)}</td>
                                ))}
                              </tr>
                            ))}
                          </tbody>
                        </table>
                        {m.reply.table.rows.length > 6 && (
                          <div className="mini-table__more">… còn {m.reply.table.rows.length - 6} dòng nữa</div>
                        )}
                      </div>
                      {m.resultKey && (
                        <button className="chip chip--action" onClick={() => setActiveKey(m.resultKey!)}>
                          📊 Mở trong lưới
                        </button>
                      )}
                    </>
                  )}

                  {/* Chart link (chart itself renders on sheet side) */}
                  {m.reply.chart && (
                    <button className="chip chip--action" onClick={() => setDisplayChart(m.reply!.chart!)}>
                      📈 Xem biểu đồ
                    </button>
                  )}

                  {m.reply.code && (
                    <details className="bubble__code">
                      <summary>Xem code Python</summary>
                      <pre>{m.reply.code}</pre>
                    </details>
                  )}

                  {/* AI's own next-question suggestions, grounded in this file/answer.
                      On a clarify turn follow_up holds the ANSWER options, already
                      rendered above - showing them again as 💡 suggestions would
                      duplicate every choice. */}
                  {!m.reply.clarify && m.reply.follow_up && m.reply.follow_up.length > 0 && (
                    <div className="bubble__follow-up" style={{ marginTop: "0.5rem", display: "flex", flexWrap: "wrap", gap: "6px" }}>
                      {m.reply.follow_up.slice(0, 3).map((q, j) => (
                        <button key={j} className="chip chip--suggest" disabled={busy} onClick={() => send(q)}>
                          💡 {q}
                        </button>
                      ))}
                    </div>
                  )}
                </>
              )}
            </div>
          ))}
          {busy && (
            <div className="bubble bubble--assistant chat-progress" aria-live="polite">
              {chatReason && <div className="chat-progress__reason">🎯 {chatReason}</div>}
              <div className="chat-progress__stage">
                <span className="chat-progress__dots"><span /><span /><span /></span>
                <span>{chatStage || "Đang kết nối..."}</span>
              </div>
              {chatThoughts.length > 0 && (
                <div className="chat-progress__thought">
                  💭 {chatThoughts[chatThoughts.length - 1]}
                </div>
              )}
            </div>
          )}
        </div>

        <div className="chat-input" data-tour="chat-input">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && send()}
            placeholder={hasData ? "Hỏi về dữ liệu, hoặc 'tạo dữ liệu...' để sinh thêm bảng mới" : "Yêu cầu AI sinh dữ liệu hoặc tải file..."}
            disabled={busy}
          />
          <button className="button" onClick={() => send()} disabled={busy}>
            Gửi
          </button>
        </div>
      </div>

      <input ref={fileRef} type="file" accept=".xlsx,.xls,.csv" multiple hidden onChange={(e) => handleUpload(e.target.files)} />
      </div>
      )}
    </>
  );
}
