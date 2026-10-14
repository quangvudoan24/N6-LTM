# Phân công & quyền sở hữu file
| Thành viên | Nhiệm vụ | File/thư mục phụ trách |
|---|---|---|
| TV1 Dũng | TCP Socket phía Server | `server/server.py` (phần socket), `prototype/` |
| TV2 Hải Huỳnh | Socket phía Client, giao thức | `agent/agent.py` (kết nối), `docs/PROTOCOL.md`, `common/protocol.py` |
| TV3 Thành Công | Heartbeat phía Agent | `agent/agent.py` (heartbeat), `docs/HEARTBEAT.md` |
| TV4 Thanh Bình | Phát hiện online/offline phía Server | `server/server.py` (monitor_loop), `docs/HEARTBEAT.md` |
| TV5 Quang Vũ | Giao diện Dashboard | `dashboard/`, `design/` |
| TV6 Gia Đạt | SQLite | `database/` |
| Nhóm trưởng | Duyệt PR, `common/constants.py`, README | |

Quy tắc: sửa file của người khác -> tạo Pull Request và gắn tên người đó review.
