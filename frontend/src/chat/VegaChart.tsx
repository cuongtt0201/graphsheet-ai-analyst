import { useEffect, useRef } from "react";
import embed from "vega-embed";

interface VegaChartProps {
  spec: Record<string, any>;
}

export default function VegaChart({ spec }: VegaChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current || !spec) return;

    // `width: "container"` is a Vega-Lite spec property (responsive width), not
    // a vega-embed option — embed's `width` is typed as a number. Put it in the
    // spec; keep the numeric height override in the embed options.
    const viewPromise = embed(
      containerRef.current,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      { width: "container", ...spec } as any,
      {
        actions: false,
        mode: "vega-lite",
        renderer: "svg",
        height: 140,
      },
    );

    return () => {
      viewPromise.then((res) => res.view.finalize()).catch(console.error);
    };
  }, [spec]);

  return <div ref={containerRef} style={{ width: "100%", minHeight: "150px" }} />;
}
