# TCP Server — TV3, tuần 2

## Chạy Server

Từ thư mục gốc repository:

```powershell
python -m server.server
```

Server dùng `SERVER_HOST` và `SERVER_PORT` trong `common/constants.py` (hiện là `0.0.0.0:9000`). Mỗi Agent được phục vụ trên một luồng riêng. Một kết nối phải gửi `REGISTER` trước `HEARTBEAT` hoặc `METRICS`; các bản tin sau đó phải giữ cùng `agent_id`.

Các trường bắt buộc và loại bản tin được kiểm tra bằng `common.protocol`. Server cũng kiểm tra chuỗi định danh/đăng ký không rỗng, timestamp là số Unix nguyên không âm, và các giá trị CPU/RAM/Disk là số trong khoảng 0–100. Bản tin được từ chối nhận `ERROR` theo cấu trúc dùng chung; bản tin được chấp nhận nhận `ACK` theo cấu trúc `{"type":"ACK","status":"ok"}`.

Yêu cầu triển khai tuần 2 quy định ACK cho mọi bản tin hợp lệ. `docs/PROTOCOL.md` hiện mô tả ACK là phản hồi cho `REGISTER`; phần mô tả chung cần được chủ sở hữu giao thức đồng bộ trước khi Agent phụ thuộc vào hành vi ACK này.

## Bàn giao cho SQLite

`create_server(..., message_handler=callback)` nhận callback với giao diện:

```python
def callback(message: dict, peer_address: tuple, received_at: int) -> None:
    ...
```

Server gọi callback một lần cho mỗi bản tin đã qua kiểm tra, trước khi gửi ACK. `received_at` là Unix timestamp giây do Server lấy lúc nhận bản tin; nên dùng cho `agents.last_seen` và thời gian sự kiện. Với METRICS, trường `timestamp` trong `message` vẫn là timestamp của mẫu do Agent gửi. Callback được gọi đồng thời từ các luồng kết nối, nên module SQLite cần quản lý kết nối SQLite phù hợp với luồng gọi. Nếu callback ném ngoại lệ, Server ghi lỗi và trả `ERROR`, không gửi ACK cho bản tin đó. Khi không truyền callback, Server chỉ kiểm tra và phản hồi giao thức, chưa lưu dữ liệu.

API lưu/query SQLite chưa có trong repository ở thời điểm triển khai này. Thành viên phụ trách DB cần cung cấp callback tương thích hoặc adapter mỏng để nối các bản tin REGISTER, HEARTBEAT và METRICS tới các thao tác ghi tương ứng.

## Kiểm thử

```powershell
pytest -q tests/test_protocol.py tests/test_server.py
```

Các kiểm thử server dùng cổng loopback động (`port=0`), bao gồm ACK/ERROR, bản tin phân mảnh và nối tiếp, xác thực trường, callback và nhiều Agent đồng thời.
