import React, { useState } from "react";
import { api } from "../api";

interface LoginModalProps {
  isOpen: boolean;
  onClose: () => void;
  onLoginSuccess: (email: string) => void;
}

export default function LoginModal({ isOpen, onClose, onLoginSuccess }: LoginModalProps) {
  const [mockEmail, setMockEmail] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!isOpen) return null;

  async function handleMockSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!mockEmail.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.mockLogin(mockEmail.trim());
      if (res.authenticated) {
        onLoginSuccess(res.email);
        onClose();
      } else {
        setError("Đăng nhập thất bại. Vui lòng thử lại!");
      }
    } catch (err: any) {
      setError(err.message || "Đăng nhập thất bại. Vui lòng thử lại!");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="login-modal-overlay">
      <div className="login-modal-card">
        <button className="login-modal-close" onClick={onClose}>&times;</button>
        
        <div className="login-modal-header">
          <div className="login-modal-icon">🔐</div>
          <h2>Đăng nhập hệ thống</h2>
          <p>Đăng nhập giúp đồng bộ tệp dữ liệu, thói quen và các chỉ số dashboard của bạn trên mọi thiết bị.</p>
        </div>

        <div className="login-modal-body">
          {/* Google login Option */}
          <a className="login-btn login-btn--google" href={api.loginUrl()}>
            <svg className="google-icon" viewBox="0 0 24 24" width="20" height="20" style={{ marginRight: "10px" }}>
              <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
              <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
              <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z"/>
              <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z"/>
            </svg>
            Tiếp tục với Google
          </a>

          <div className="login-divider">
            <span>Hoặc</span>
          </div>

          {/* Quick Mock Login Option */}
          <form onSubmit={handleMockSubmit} className="login-mock-form">
            <label>Đăng nhập nhanh bằng Email</label>
            <div className="login-input-group">
              <input
                type="email"
                required
                placeholder="developer@example.com"
                value={mockEmail}
                onChange={(e) => setMockEmail(e.target.value)}
                disabled={loading}
              />
              <button type="submit" className="button button--login-submit" disabled={loading}>
                {loading ? "..." : "Xác nhận"}
              </button>
            </div>
            {error && <div className="login-error">{error}</div>}
          </form>
        </div>
      </div>
    </div>
  );
}
