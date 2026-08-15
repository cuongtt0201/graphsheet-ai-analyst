import { useEffect, useState } from "react";
import "./App.css";
import ChatWorkspace from "./chat/ChatWorkspace";
import LandingPage from "./chat/LandingPage";
import { api } from "./api";
import SwarmMonitor from "./admin/SwarmMonitor";

function App() {
  const [email, setEmail] = useState<string | null>(null);
  const [checking, setChecking] = useState(true);

  if (window.location.pathname === "/admin/swarm") {
    return <SwarmMonitor />;
  }

  useEffect(() => {
    api
      .me()
      .then((res) => setEmail(res.authenticated ? res.email ?? null : null))
      .catch(() => setEmail(null))
      .finally(() => setChecking(false));
  }, []);

  if (checking) {
    return (
      <div style={{
        display: "flex",
        height: "100vh",
        alignItems: "center",
        justifyContent: "center",
        background: "radial-gradient(circle at 20% 30%, #1e1b4b 0%, #0f172a 100%)",
        color: "#ffffff",
        fontFamily: "system-ui, sans-serif"
      }}>
        Đang tải...
      </div>
    );
  }

  if (!email) {
    return <LandingPage onLoginSuccess={(email) => setEmail(email)} />;
  }

  return (
    <div className="app app--chat">
      <ChatWorkspace email={email} onLogout={() => setEmail(null)} />
    </div>
  );
}

export default App;
