import React, { useEffect, useState } from "react";
import "./App.css";
import ChatWorkspace from "./chat/ChatWorkspace";
import LandingPage from "./chat/LandingPage";
import { api } from "./api";

interface GlobalErrorBoundaryProps {
  children: React.ReactNode;
}

interface GlobalErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

class GlobalErrorBoundary extends React.Component<GlobalErrorBoundaryProps, GlobalErrorBoundaryState> {
  constructor(props: GlobalErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): GlobalErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error("Global UI crash caught:", error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          display: "flex",
          flexDirection: "column",
          height: "100vh",
          alignItems: "center",
          justifyContent: "center",
          background: "radial-gradient(circle at 20% 30%, #1e1b4b 0%, #0f172a 100%)",
          color: "#ffffff",
          fontFamily: "system-ui, sans-serif",
          padding: "20px",
          textAlign: "center",
        }}>
          <div style={{
            background: "rgba(255, 255, 255, 0.05)",
            border: "1px solid rgba(255, 255, 255, 0.15)",
            borderRadius: "16px",
            padding: "32px",
            maxWidth: "500px",
            backdropFilter: "blur(10px)",
          }}>
            <h2 style={{ fontSize: "1.4rem", marginBottom: "12px", color: "#f43f5e" }}>⚠️ Đã xảy ra lỗi giao diện</h2>
            <p style={{ fontSize: "0.9rem", color: "#94a3b8", marginBottom: "20px" }}>
              {this.state.error?.message || "Đã xảy ra lỗi không xác định khi hiển thị dữ liệu."}
            </p>
            <button
              onClick={() => window.location.reload()}
              style={{
                background: "#4f46e5",
                color: "#ffffff",
                border: "none",
                borderRadius: "8px",
                padding: "10px 24px",
                fontSize: "0.9rem",
                fontWeight: 600,
                cursor: "pointer",
                boxShadow: "0 4px 14px rgba(79, 70, 229, 0.4)",
              }}
            >
              🔄 Tải lại trang
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

function App() {
  const [email, setEmail] = useState<string | null>(null);
  const [checking, setChecking] = useState(true);

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
    <GlobalErrorBoundary>
      <div className="app app--chat">
        <ChatWorkspace email={email} onLogout={() => setEmail(null)} />
      </div>
    </GlobalErrorBoundary>
  );
}

export default App;
