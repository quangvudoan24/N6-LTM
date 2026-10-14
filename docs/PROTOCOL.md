# Đặc tả giao thức (v1.0)
Chủ sở hữu: TV1 + TV2. Muốn đổi -> Pull Request + báo cả nhóm.

## Quy ước chung
- Truyền qua **TCP**, cổng **9000**.
- Mỗi tin nhắn là **1 dòng JSON UTF-8, kết thúc bằng `\n`**.
- `timestamp`: số giây Unix (int). Phần trăm (`cpu`, `ram`, `disk`): số thực 0–100.
- Dùng `common/protocol.py` để mã hóa/giải mã, **không tự viết lại**.

## Các loại tin nhắn
| type | Hướng | Trường bắt buộc | Khi nào gửi |
|---|---|---|---|
| REGISTER | Agent → Server | agent_id, hostname, ip, os | Ngay sau khi kết nối |
| ACK | Server → Agent | status | Phản hồi REGISTER |
| HEARTBEAT | Agent → Server | agent_id, timestamp | Mỗi 5s |
| METRICS | Agent → Server | agent_id, cpu, ram, disk, timestamp | Mỗi 5s |
| ERROR | Server → Agent | message | Khi tin nhắn sai |

## Ví dụ
```json
{"type": "REGISTER", "agent_id": "PC-01", "hostname": "may-dung", "ip": "192.168.1.10", "os": "Windows 11"}
{"type": "ACK", "status": "ok"}
{"type": "HEARTBEAT", "agent_id": "PC-01", "timestamp": 1760000000}
{"type": "METRICS", "agent_id": "PC-01", "cpu": 35.2, "ram": 61.0, "disk": 70.5, "timestamp": 1760000005}
```

## Luồng kết nối
1. Agent connect → gửi REGISTER
2. Server lưu vào bảng `agents`, trả ACK, đặt status = ONLINE
3. Agent gửi HEARTBEAT + METRICS định kỳ
4. Mất kết nối: Agent thử lại sau 3s, 5s, 10s (giữ 10s)

## Trạng thái máy
| Trạng thái | Điều kiện (now − last_seen) |
|---|---|
| ONLINE | ≤ 10s |
| WARNING | > 10s và ≤ 15s |
| OFFLINE | > 15s |

## Ánh xạ JSON ↔ cột DB
| JSON | Bảng.cột |
|---|---|
| agent_id | agents.agent_id / metrics.agent_id |
| hostname | agents.hostname |
| ip | agents.ip_address |
| os | agents.os |
| cpu, ram, disk, timestamp | metrics.cpu, .ram, .disk, .timestamp |
