import { useEffect, useState, useRef } from "react";
import "./SwarmMonitor.css";

interface SwarmEvent {
  timestamp: number;
  agent: string;
  type: string;
  message: string;
  metadata: any;
}

export default function SwarmMonitor() {
  const [events, setEvents] = useState<SwarmEvent[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const logEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const sse = new EventSource("/api/agent/swarm-stream");
    
    sse.onopen = () => setIsConnected(true);
    sse.onerror = () => setIsConnected(false);
    
    sse.onmessage = (e) => {
      try {
        const event: SwarmEvent = JSON.parse(e.data);
        setEvents((prev) => [...prev, event].slice(-200)); // Keep last 200 logs
      } catch (err) {}
    };

    return () => sse.close();
  }, []);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events]);

  const agents = ["DataAgent", "CodeAgent", "PoolRouter"];

  return (
    <div className="swarm-admin">
      <div className="swarm-header">
        <h1>🧠 TỔ ĐÀN AI - SWARM MONITOR</h1>
        <div className={`status-badge ${isConnected ? "live" : "offline"}`}>
          {isConnected ? "● LIVE TELEMETRY" : "○ OFFLINE"}
        </div>
      </div>

      <div className="agent-panels">
        {agents.map((agent) => {
          const agentEvents = events.filter(e => e.agent === agent);
          const lastEvent = agentEvents[agentEvents.length - 1];
          const isActive = lastEvent && (Date.now() / 1000 - lastEvent.timestamp < 5); // Active if event in last 5s

          return (
            <div key={agent} className={`agent-card ${isActive ? "active" : ""}`}>
              <h3>{agent} {isActive && <span className="pulse"></span>}</h3>
              <div className="agent-last-thought">
                {lastEvent ? lastEvent.message.slice(0, 80) + "..." : "Đang chờ..."}
              </div>
            </div>
          );
        })}
      </div>

      <div className="log-container">
        <div className="log-header">LIVE THOUGHT STREAM</div>
        <div className="log-list">
          {events.map((e, idx) => (
            <div key={idx} className={`log-entry type-${e.type}`}>
              <span className="log-time">[{new Date(e.timestamp * 1000).toISOString().split("T")[1].slice(0, -1)}]</span>
              <span className="log-agent">{e.agent}</span>
              <span className="log-msg">{e.message}</span>
              {e.metadata && Object.keys(e.metadata).length > 0 && (
                <pre className="log-meta">{JSON.stringify(e.metadata, null, 2)}</pre>
              )}
            </div>
          ))}
          <div ref={logEndRef} />
        </div>
      </div>
    </div>
  );
}
