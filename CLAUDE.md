# GraphSheet — hướng dẫn cho AI agent

AI Data Analyst & Dashboard Tool. FastAPI (Python) + React/Vite (TypeScript), dual-tier sandbox, Neo4j graph memory.
Dự án **không dùng git** — quản lý source local. Không có undo, nên **backup trước khi ghi đè** bất cứ file nào.

---

## Quy trình bắt buộc trước khi đọc/sửa code

Dự án có knowledge graph ở `.ua/knowledge-graph.json` (631 node, 1.274 edge). **Dùng nó thay cho việc đọc code mò.**

**Bước 1 — kiểm tra graph còn tin được không:**

```bash
node .ua/enrich-graph.mjs --check
```

So sha1 từng file với hash lúc build. Nếu báo `[sua]`/`[moi]`/`[xoa]` ở file nào thì **file đó không được tin graph** — đọc code thật. Các file còn lại vẫn dùng graph bình thường.

**Bước 2 — tra graph, đừng grep mò:**

```bash
node .ua/q.mjs find <từ-khoá>     # tính năng/symbol nằm ở file nào
node .ua/q.mjs impact <file|sym>  # SỬA cái này thì VỠ chỗ nào (thêm --deep để lần 3 tầng)
node .ua/q.mjs deps <file>        # file này phụ thuộc vào gì
node .ua/q.mjs file <file>        # tóm tắt 1 file: symbol + ai dùng + dùng gì
node .ua/q.mjs hubs               # file bị phụ thuộc nhiều nhất
```

Mỗi lệnh tốn ~150-250 token, thay cho 3.000-25.000 token đọc file.

**Luôn chạy `impact` trước khi sửa một hàm dùng chung.** Ví dụ `q.mjs impact agent/sandbox.py` → 13 file bị ảnh hưởng.

### Cấm

- **KHÔNG** `Read` cả `.ua/knowledge-graph.json` — 386 KB ≈ **110.000 token**. Luôn query qua `q.mjs`.
- **KHÔNG** đụng `repomix-output.xml` — 58 MB.
- `repomix-clean.md` (938 KB ≈ 245k token) chỉ dùng để attach cho AI ngoài (ChatGPT/Gemini), **không** đọc trong Claude Code.
- **KHÔNG** đọc `.ua/tmp/`, `.ua/intermediate/`, `backend/app/storage/`, `__pycache__`, `node_modules`.

### Build lại graph sau khi code xong một đợt

```bash
node .ua/tmp/build-graph.mjs && node .ua/enrich-graph.mjs
```

`.ua/enrich-graph.mjs` **phải chạy sau**, vì plugin không sinh edge `imports`/`calls`.

**Giới hạn quan trọng:** `build-graph.mjs` chỉ extract lại đúng danh sách file trong `.ua/intermediate/batches.json` — kết quả của lần quét **đầu tiên**. Nó **không quét lại thư mục**, nên file MỚI tạo sẽ không có node/symbol.

`enrich-graph.mjs` vá một nửa: nó tự thêm **node file + cạnh imports** cho file mới (summary sẽ ghi rõ "chưa có symbol"). Muốn có cả **symbol bên trong** file mới thì phải **chạy lại plugin Understand Anything** để sinh lại `batches.json`.

`.ua/tmp/build-graph.mjs` nằm trong thư mục tạm, có thể bị plugin xoá — mất thì chạy lại plugin.

---

## Sandbox — phải build image, không compose lo hộ

`agent/sandbox.py` có 2 tầng. Tầng 1 chạy code AI sinh ra trong **container riêng**; tầng 2 chạy **ngay trong tiến trình backend**, chỉ có `scan_code` (quét AST) che chắn.

`docker-compose.yml` **không** build image sandbox — nó chỉ mount `/var/run/docker.sock` để backend sinh container con. Thiếu image thì hệ thống **im lặng tụt xuống tầng 2**, vẫn chạy đúng nhưng mất cách ly ở mức hệ điều hành.

```bash
docker build -f backend/Dockerfile.sandbox -t ai-dashboard-sandbox backend/
```

Build một lần trên mỗi máy. Kiểm tra: `docker images | grep sandbox`.

---

## Bản đồ tính năng → file

### Backend (`backend/app/`)

| Tính năng | File |
|---|---|
| Entry FastAPI, mount router | `main.py`, `state.py`, `config.py` |
| Upload & parse file | `routers/upload.py` (phụ thuộc nhiều nhất: 20 file) |
| Chat endpoint | `routers/chat.py` |
| Dashboard/agent endpoint | `routers/agent.py` |
| Vòng lặp chat chính | `agent/chat_agent.py` |
| Code interpreter | `agent/code_interpreter.py` |
| **Sandbox chạy code** | `agent/sandbox.py` — hub, 7 file import |
| Sub-agent / swarm | `agent/sub_agents.py`, `agent/swarm.py`, `agent/swarm_monitor.py` |
| Investigator / goal explorer | `agent/investigator.py`, `agent/goal_explorer.py` |
| Skill động | `agent/skills_manager.py`, `skills/curated/`, `skills/personal/` |
| Nén schema cho LLM | `agent/babeltele.py` |
| Chart (25 loại) | `agent/chart_utils.py` ↔ `frontend/src/chat/MiniChart.tsx` |
| Format số, report | `agent/number_format.py`, `agent/report.py` |
| Lọc dashboard | `agent/dashboard_filter.py` |
| **Pool key Gemini** | `ai/pool.py` — hub lớn nhất, 14 file import |
| **Harness (verify_numbers)** | `ai/harness.py` — 10 file import |
| Prompt | `ai/prompts.py` |
| Đọc/profil dữ liệu | `data/profiling.py`, `data/profiler.py`, `data/smart_read.py` |
| **Sửa encoding/mojibake** | `data/encoding_fix.py` — chỉ `data/profiling.py` dùng |
| Semantic, entity, quan hệ | `data/semantics.py`, `data/entities.py`, `data/relations.py` |
| Trend, EDA, merge, join | `data/trends.py`, `data/eda.py`, `data/merge.py`, `data/join_guard.py` |
| Header bằng LLM | `data/llm_header.py` |
| Context dựng cho LLM | `data/context.py`, `data/dispatcher.py` |
| **Neo4j graph memory** | `memory/graph.py`, `memory/idle_job.py` |
| Google Sheets, export xlsx | `sheets/client.py`, `sheets/xlsx_export.py` |
| OAuth | `auth/oauth.py`, `auth/routes.py` |

### Frontend (`frontend/src/`)

| Tính năng | File |
|---|---|
| **Workspace chat chính** | `chat/ChatWorkspace.tsx` — **2.303 dòng / 98 KB ≈ 25k token** |
| Gọi API backend | `api.ts` — hub frontend |
| Chart | `chat/MiniChart.tsx` (33 KB), `chat/VegaChart.tsx` |
| Bảng tính Univer | `chat/UniverGrid.tsx` |
| BI explore | `chat/BIExplore.tsx` |
| Landing, login, auth | `chat/LandingPage.tsx`, `chat/LoginModal.tsx`, `chat/AuthBar.tsx` |
| Tour, skeleton | `chat/Tour.tsx`, `chat/SkeletonGrid.tsx` |
| Admin | `admin/` |

---

## File to — đọc theo lát cắt, đừng đọc trọn

`ChatWorkspace.tsx` (98 KB), `code_interpreter.py` (35 KB), `chat_agent.py` (34 KB), `MiniChart.tsx` (33 KB), `upload.py` (26 KB), `sandbox.py` (24 KB).

Với những file này: `q.mjs file <tên>` lấy danh sách symbol trước, rồi `Grep` định vị dòng, rồi `Read` với `offset`/`limit`. Đừng `Read` cả file.

---

## Giới hạn đã biết của graph

Cố ý bỏ để tránh edge sai — gặp mấy ca này thì fallback sang `Grep`:

- **55 lời gọi nhập nhằng** (trùng tên hàm ở nhiều file) bị loại. Thà thiếu còn hơn sai.
- **Không có liên kết cross-language.** Frontend ↔ backend nối nhau qua HTTP route, graph không thấy. Muốn dò luồng `ChatWorkspace.tsx` → router nào thì phải `Grep` đường dẫn API trong `api.ts`.
- `calls` chỉ có cho 92/137 file (chỗ extractor sinh được `callGraph`). `imports` thì đủ, vì parse thẳng từ source.
