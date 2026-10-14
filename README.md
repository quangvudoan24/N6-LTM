# Hệ thống giám sát mạng Client-Server

Agent (Client) thu thập CPU/RAM/Disk + gửi heartbeat → Server (TCP) → SQLite → Dashboard.

```
[Agent 1] ──┐
[Agent 2] ──┼── TCP:9000 ──> [Server] ──> [SQLite] ──> [Dashboard]
[Agent 3] ──┘
```

## Cài đặt (mỗi thành viên làm 1 lần)
```bash
git clone <URL-repo>
cd network-monitor
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux/macOS
pip install -r requirements.txt
cp .env.example .env            # Windows: copy .env.example .env
python -m database.init_db      # tạo DB
pytest                          # kiểm tra môi trường OK
```

## Chạy thử
```bash
python -m server.server
python -m agent.agent
```
(Luôn chạy từ thư mục gốc repo, dùng `python -m ...`.)

## Cấu trúc thư mục
| Thư mục | Nội dung |
|---|---|
| `common/` | Hằng số + giao thức dùng chung (**không tự viết lại**) |
| `server/` | Server TCP + giám sát heartbeat |
| `agent/` | Agent thu thập số liệu |
| `database/` | `schema.sql`, truy vấn mẫu, `init_db.py` |
| `dashboard/` | Giao diện |
| `docs/` | Đặc tả giao thức, tài liệu heartbeat, phân công |
| `design/` | Wireframe, sơ đồ (ERD, sequence, state machine) |
| `prototype/` | Code thử nghiệm tuần 1 |
| `tests/` | Kiểm thử |

## Tài liệu quan trọng (đọc trước khi code)
- [`docs/PROTOCOL.md`](docs/PROTOCOL.md) – định dạng tin nhắn
- [`docs/TEAM.md`](docs/TEAM.md) – ai phụ trách gì
- [`CONTRIBUTING.md`](CONTRIBUTING.md) – quy tắc Git
- [`common/constants.py`](common/constants.py) – thông số dùng chung
