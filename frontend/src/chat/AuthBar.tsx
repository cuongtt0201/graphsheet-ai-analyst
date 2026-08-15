import { api } from "../api";

interface AuthBarProps {
  email: string | null;
  onLogout: () => void;
}

export default function AuthBar({ email, onLogout }: AuthBarProps) {
  async function handleLogout() {
    try {
      await api.logout();
    } finally {
      onLogout();
    }
  }

  if (!email) return null;

  return (
    <div className="auth-bar">
      <span className="auth-bar__email" title="Đăng nhập giúp nhớ thói quen/dashboard của bạn trên mọi thiết bị">
        👤 {email}
      </span>
      <button className="auth-bar__btn auth-bar__btn--ghost" onClick={handleLogout}>
        Đăng xuất
      </button>
    </div>
  );
}
