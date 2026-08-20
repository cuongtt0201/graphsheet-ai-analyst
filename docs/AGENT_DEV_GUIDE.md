# GraphSheet — Hướng dẫn cho AI Agent & Developer

AI Data Analyst & Dashboard Tool. FastAPI (Python) + React/Vite (TypeScript), dual-tier sandbox, Neo4j graph memory.

---

## 🛠️ Quy trình phát triển & Sửa mã nguồn

1. **Quản lý source bằng Git**: Sử dụng các lệnh Git tiêu chuẩn (`git status`, `git diff`, `git log`).
2. **Kỷ luật Grounding & Sandbox**:
   - Dữ liệu qua biên sandbox chỉ sử dụng Parquet và JSON (`NEVER pickle`).
   - Mọi câu trả lời định lượng qua chat bắt buộc phải qua `verify_numbers` của `ai/harness.py`.
   - Chạy kiểm thử sau mỗi thay đổi:
     ```bash
     cd backend && pytest tests/test_learner.py tests/test_alpha.py tests/test_critic.py -v
     ```

---

## 📦 Sandbox — Phải build image riêng

`agent/sandbox.py` có 2 tầng:
- **Tầng 1 (Production)**: Chạy code Python sinh ra trong **Docker Container riêng biệt** với giới hạn tài nguyên và thời gian thực thi (timeout).
- **Tầng 2 (Fallback)**: Chạy trong tiến trình backend với AST security scanning (`scan_code`).

Để kích hoạt Tầng 1:
```bash
docker build -f backend/Dockerfile.sandbox -t ai-dashboard-sandbox backend/
```
Kiểm tra image đã build: `docker images | grep sandbox`.

---

## 🗺️ Bản đồ tính năng → File

### Backend (`backend/app/`)

| Phân hệ | File chính |
|---|---|
| Entry FastAPI, router | `main.py`, `state.py`, `config.py` |
| Upload & Parse file | `routers/upload.py`, `data/smart_read.py`, `data/profiling.py` |
| Chat & Alpha Orchestrator | `routers/chat.py`, `agent/alpha.py`, `agent/chat_agent.py` |
| Deterministic Critic | `agent/critic.py` (0ms statistical outlier & Pareto guards) |
| Autonomous Learner & Memory | `memory/learner.py`, `memory/graph.py` (Neo4j Bubble Merge) |
| Python Sandbox | `agent/sandbox.py`, `agent/code_interpreter.py` |
| AI Key Pool & Load Balancer | `ai/pool.py` (Affinity bonus, fallback penalty) |
| Harness & Number Verifier | `ai/harness.py` (`verify_numbers`, `collect_ground_truth`) |
| Data & Semantics | `data/semantics.py`, `data/trends.py`, `data/join_guard.py` |
| Visualization & Charts | `agent/chart_utils.py` (Vega-Lite & 25 loại chart) |

### Frontend (`frontend/src/`)

| Phân hệ | File chính |
|---|---|
| Workspace Chat & AI Stream | `chat/ChatWorkspace.tsx` |
| API Client | `api.ts` |
| Trực quan hoá & Charts | `chat/MiniChart.tsx`, `chat/VegaChart.tsx` |
| Grid Bảng tính | `chat/UniverGrid.tsx` |
| Khám phá Dữ liệu | `chat/BIExplore.tsx` |

---

## 🧪 Testing Guidelines
Mọi module khi phát triển hoặc refactor đều phải có unit test tương ứng trong `backend/tests/`:
- `tests/test_learner.py`: Kiểm thử trích xuất luật, thói quen & Neo4j graph.
- `tests/test_alpha.py`: Kiểm thử luồng nhận thức Alpha, code retries, mind shifts & vega chart.
- `tests/test_critic.py`: Kiểm thử Pareto concentration & IQR/adaptive Z-score outliers.
