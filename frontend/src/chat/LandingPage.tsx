import React, { useState, useEffect } from "react";
import { api } from "../api";

interface LandingPageProps {
  onLoginSuccess: (email: string) => void;
}

export default function LandingPage({ onLoginSuccess }: LandingPageProps) {
  const [mockEmail, setMockEmail] = useState("");
  const [showDevLogin, setShowDevLogin] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Background spreadsheet data grid (12 rows x 8 columns)
  const [cellData, setCellData] = useState<number[][]>([]);
  
  // Initialize spreadsheet cell data
  useEffect(() => {
    const initData = Array.from({ length: 12 }, () => 
      Array.from({ length: 8 }, () => Math.floor(Math.random() * 850) + 100)
    );
    setCellData(initData);
  }, []);

  // Soft flicker loop for background cell values
  useEffect(() => {
    const flicker = setInterval(() => {
      setCellData((prev) => 
        prev.map((row) => 
          row.map((val) => 
            Math.random() > 0.85 ? Math.floor(Math.random() * 950) + 50 : val
          )
        )
      );
    }, 500);
    return () => clearInterval(flicker);
  }, []);

  async function handleMockSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!mockEmail.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.mockLogin(mockEmail.trim());
      if (res.authenticated) {
        onLoginSuccess(res.email);
      } else {
        setError("Đăng nhập thất bại.");
      }
    } catch (err: any) {
      setError(err.message || "Lỗi kết nối.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="landing-screen">
      
      {/* 1. Full-screen Background Spreadsheet Matrix running silently */}
      <div className="fullscreen-bg-spreadsheet">
        {cellData.map((row, rIdx) => (
          <div key={rIdx} className="bg-sheet-row">
            {row.map((val, cIdx) => {
              // Place mini bar charts in specific cells
              const isChartCell = (rIdx === 2 && cIdx === 1) || 
                                  (rIdx === 5 && cIdx === 6) || 
                                  (rIdx === 8 && cIdx === 3) ||
                                  (rIdx === 10 && cIdx === 5);
              return (
                <div key={cIdx} className="bg-sheet-cell">
                  {isChartCell ? (
                    <div className="bg-cell-minichart">
                      <span className="minibar" style={{ height: `${(val % 60) + 20}%` }}></span>
                      <span className="minibar" style={{ height: `${((val * 1.5) % 70) + 25}%` }}></span>
                      <span className="minibar" style={{ height: `${((val * 0.7) % 50) + 15}%` }}></span>
                    </div>
                  ) : (
                    <span className="bg-cell-number">{val}</span>
                  )}
                </div>
              );
            })}
          </div>
        ))}
      </div>

      {/* 2. Floating Dynamic Background Charts (Layer 1) */}
      <div className="bg-dynamic-charts-layer">
        
        {/* Faded Bar Chart (Top Right area) */}
        <div className="bg-float-chart float-bars">
          <div className="float-bar" style={{ animationDelay: "0.1s" }}></div>
          <div className="float-bar" style={{ animationDelay: "0.4s" }}></div>
          <div className="float-bar" style={{ animationDelay: "0.2s" }}></div>
          <div className="float-bar" style={{ animationDelay: "0.6s" }}></div>
          <div className="float-bar" style={{ animationDelay: "0.3s" }}></div>
        </div>

        {/* Faded Line Wave Chart (Bottom Left area) */}
        <div className="bg-float-chart float-lines">
          <svg viewBox="0 0 200 100" className="float-line-svg">
            <path 
              d="M 10 80 Q 50 15 90 65 T 180 35" 
              fill="none" 
              stroke="url(#line-grad-bg)" 
              strokeWidth="3.5" 
              strokeLinecap="round"
              className="float-line-path"
            />
            <circle cx="90" cy="65" r="4.5" fill="#3b82f6" className="float-line-point" />
            <circle cx="180" cy="35" r="4.5" fill="#10b981" className="float-line-point-fast" />
            <defs>
              <linearGradient id="line-grad-bg" x1="0" y1="0" x2="1" y2="0">
                <stop offset="0%" stopColor="#3b82f6" />
                <stop offset="100%" stopColor="#10b981" />
              </linearGradient>
            </defs>
          </svg>
        </div>

        {/* Faded Pie/Radial segmented chart (Bottom Right area) */}
        <div className="bg-float-chart float-pie">
          <svg viewBox="0 0 100 100" className="float-pie-svg">
            <circle cx="50" cy="50" r="38" stroke="rgba(255,255,255,0.03)" strokeWidth="6" fill="none" />
            <circle 
              cx="50" cy="50" r="38" 
              stroke="#8b5cf6" 
              strokeWidth="6" 
              fill="none" 
              strokeDasharray="238" 
              strokeDashoffset="80"
              className="float-pie-circle-violet"
            />
            <circle 
              cx="50" cy="50" r="28" 
              stroke="#ec4899" 
              strokeWidth="4" 
              fill="none" 
              strokeDasharray="175" 
              strokeDashoffset="50"
              className="float-pie-circle-pink"
            />
          </svg>
        </div>

      </div>

      <div className="bg-gradient-glows">
        <div className="glow-spot spot-blue"></div>
        <div className="glow-spot spot-purple"></div>
      </div>

      {/* 2-Column Split Layout */}
      <div className="landing-split-layout two-cols">
        
        {/* Left Column: Title and Actions */}
        <div className="landing-col-left">
          <div className="landing-brand-header">
            <span className="brand-badge">GraphSheet AI</span>
          </div>
          <h1 className="landing-hero-title">GraphSheet</h1>
          <p className="landing-hero-desc">
            Trợ lý tự trị phân tích bảng tính với trí nhớ đồ thị dài hạn. <br/>
            Tải lên Excel, đặt câu hỏi và nhận báo cáo trực quan tức thì.
          </p>

          <div className="landing-action-buttons-row">
            <a className="landing-btn-primary" href={api.loginUrl()}>
              <svg className="google-icon-svg" viewBox="0 0 24 24" width="20" height="20">
                <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
                <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
                <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z"/>
                <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z"/>
              </svg>
              Login with Google
            </a>

            {!showDevLogin ? (
              <button className="landing-btn-secondary" onClick={() => setShowDevLogin(true)}>
                Dev Mock Login
              </button>
            ) : (
              <form onSubmit={handleMockSubmit} className="landing-inline-dev-form">
                <input
                  type="email"
                  required
                  placeholder="Nhập email phát triển..."
                  value={mockEmail}
                  onChange={(e) => setMockEmail(e.target.value)}
                  disabled={loading}
                />
                <button type="submit" disabled={loading}>
                  {loading ? "..." : "Go"}
                </button>
              </form>
            )}
          </div>
          {error && <div className="landing-error-box">{error}</div>}
        </div>

        {/* Right Column: 3D Holographic Cube */}
        <div className="landing-col-right-cube">
          <div className="landing-aura-glow"></div>
          
          <div className="scene-3d">
            <div className="cube-3d">
              {/* Front Face: Grid values */}
              <div className="cube-face cube-front">
                <div className="cube-grid-overlay"></div>
                <div className="cube-grid-content">
                  <div className="grid-header-row">
                    <span>A</span><span>B</span><span>C</span><span>D</span>
                  </div>
                  <div className="grid-body-row">
                    <span>Q3</span><span>$9.4k</span><span className="glow-val">📈</span><span>142</span>
                  </div>
                  <div className="grid-body-row">
                    <span>Q4</span><span>$12k</span><span className="glow-val">🚀</span><span>189</span>
                  </div>
                </div>
              </div>

              {/* Back Face: Bar Chart */}
              <div className="cube-face cube-back">
                <div className="cube-chart-container">
                  <span className="cube-bar" style={{ height: "45%" }}></span>
                  <span className="cube-bar" style={{ height: "70%" }}></span>
                  <span className="cube-bar" style={{ height: "55%" }}></span>
                  <span className="cube-bar" style={{ height: "85%" }}></span>
                </div>
              </div>

              {/* Top Face: Node Map */}
              <div className="cube-face cube-top">
                <div className="cube-nodes-wrap">
                  <div className="cube-node-dot" style={{ top: "30%", left: "30%" }}></div>
                  <div className="cube-node-dot" style={{ top: "65%", left: "68%" }}></div>
                  <div className="cube-node-dot" style={{ top: "45%", left: "50%" }}></div>
                  <svg className="cube-node-svg">
                    <line x1="30%" y1="30%" x2="50%" y2="45%" stroke="rgba(255,255,255,0.4)" strokeWidth="1.5" />
                    <line x1="50%" y1="45%" x2="68%" y2="65%" stroke="rgba(255,255,255,0.4)" strokeWidth="1.5" />
                  </svg>
                </div>
              </div>

              {/* Bottom Face */}
              <div className="cube-face cube-bottom"></div>
              {/* Left Face */}
              <div className="cube-face cube-left"></div>
              {/* Right Face */}
              <div className="cube-face cube-right"></div>
            </div>
          </div>
        </div>

      </div>
    </div>
  );
}
