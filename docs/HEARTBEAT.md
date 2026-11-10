# Cơ chế Heartbeat và trạng thái thiết bị — TV4 Week 3

Tài liệu này mô tả cách [`server/server.py`](../server/server.py) và [`database/operations.py`](../database/operations.py) hiện xử lý heartbeat. Các hằng số nằm trong [`common/constants.py`](../common/constants.py); tài liệu protocol dùng chung vẫn do chủ sở hữu protocol quản lý.

## Chu kỳ và ngưỡng

| Hằng số | Giá trị hiện tại | Ý nghĩa |
|---|---:|---|
| `HEARTBEAT_INTERVAL` | 5 giây | Chu kỳ Agent dự kiến gửi HEARTBEAT. |
| `WARNING_AFTER` | 10 giây | Quá 10 giây từ tín hiệu tham chiếu thì trạng thái là WARNING. |
| `OFFLINE_AFTER` | 15 giây | Quá 15 giây từ tín hiệu tham chiếu thì trạng thái là OFFLINE. |
| `MONITOR_SCAN_INTERVAL` | 5 giây | Khoảng chờ giữa các lần quét monitor. Monitor chạy một lần ngay khi khởi động thread, sau đó chờ theo chu kỳ này. |

Status logic được tính theo tuổi tín hiệu tham chiếu:

| Tuổi tín hiệu | Status |
|---|---|
| `age <= 10` giây | ONLINE |
| `10 < age <= 15` giây | WARNING |
| `age > 15` giây | OFFLINE |

Các ngưỡng là điều kiện logic; SQLite được cập nhật ở lần monitor scan kế tiếp. Ở điều kiện bình thường, thời gian nhận biết có thể trễ gần một chu kỳ quét, cộng thêm thời gian xử lý/đợi DB. Nếu scan lỗi hoặc bị chặn, status có thể cập nhật muộn hơn.

## REGISTER, Heartbeat và `last_seen`

- Khi một TCP connection REGISTER hợp lệ, Server ghi/cập nhật thông tin thiết bị và status ban đầu ONLINE. `agents.last_seen` không được đặt bởi REGISTER.
- Sau khi DB ghi REGISTER thành công, Server lưu `received_at` của lần REGISTER vào `ServerState.registration_times` trong bộ nhớ. Monitor dùng mốc này trước HEARTBEAT đầu tiên; không dùng `created_at` hoặc `last_seen` cũ làm mốc phiên mới.
- Mỗi connection chỉ được REGISTER một lần. REGISTER lặp trên cùng connection nhận ERROR rồi connection đóng. Một connection mới có thể REGISTER lại cùng `agent_id`; nó nhận generation mới và thay thế session hiện hành.
- HEARTBEAT hợp lệ của session hiện hành đặt status ONLINE và cập nhật `agents.last_seen` bằng thời điểm Server xử lý message (`received_at`), không dùng timestamp trong HEARTBEAT của Agent. Server không gửi ACK thành công cho HEARTBEAT.
- Sau HEARTBEAT đầu tiên, Server xóa mốc REGISTER tạm trong bộ nhớ; các lần monitor sau dùng `last_seen` trong SQLite.
- METRICS chỉ thêm mẫu vào `metrics`, dùng timestamp mẫu do Agent gửi. METRICS không cập nhật `last_seen` hoặc status và không nhận ACK thành công.

[`docs/PROTOCOL.md`](PROTOCOL.md) mô tả các ngưỡng bằng `now - last_seen`. Implementation hiện có thêm mốc REGISTER trong bộ nhớ cho khoảng trước HEARTBEAT đầu tiên, khi `last_seen` vẫn NULL. Đây là chi tiết trạng thái nội bộ Server; tài liệu này không thay đổi protocol contract dùng chung.

## Reconnect, session stale và disconnect

- Reconnect dùng TCP connection mới. REGISTER mới cập nhật hostname/IP/OS và status ban đầu ONLINE, giữ nguyên `created_at` và `last_seen`, đồng thời thay generation/mốc REGISTER trong bộ nhớ.
- Heartbeat hoặc Metrics từ generation cũ không được ghi DB. Khi phát hiện stale session, Server gửi một ERROR rồi đóng connection; các frame còn lại trên socket đó không được xử lý.
- Cleanup chỉ xóa generation và mốc REGISTER nếu session đóng vẫn là generation hiện hành. Disconnect không tự ghi status OFFLINE ngay; monitor tiếp tục dựa trên mốc REGISTER còn hiệu lực hoặc `last_seen` gần nhất.

## Hành vi khi Server khởi động lại

`active_sessions`, `registration_times` và generation counter chỉ tồn tại trong bộ nhớ, nên mất khi process Server dừng. `init_db()` không xóa dữ liệu SQLite; monitor chạy ngay sau khi Server khởi động và đánh giá lại các Agent:

- Bản ghi có `last_seen IS NULL` và không có mốc REGISTER trong process mới được đặt OFFLINE trong DB ở lần monitor scan đầu tiên (nếu hiện chưa OFFLINE).
- Bản ghi có `last_seen` khác NULL được phân loại theo tuổi của `last_seen`; status sẽ là ONLINE, WARNING hoặc OFFLINE theo các ngưỡng ở trên. Heartbeat cuối còn mới có thể khiến record vẫn ONLINE/WARNING sau restart; không phải mọi record cũ đều bị ép OFFLINE.
- Khi Agent REGISTER lại sau restart, Server tạo mốc REGISTER mới trong bộ nhớ. `created_at` không được dùng làm mốc liveness.

## Đồng bộ và lỗi

Server dùng cùng một lock để tuần tự hóa ghi message, thay generation/mốc REGISTER và monitor scan. `received_at` được lấy sau khi lock được giữ, trước khi gọi callback SQLite, để callback được ghi theo đúng thứ tự xử lý. HEARTBEAT stale và REGISTER lặp không tới callback DB.

Nếu lưu REGISTER thất bại, Server trả ERROR và không ACK thành công. HEARTBEAT/METRICS hợp lệ được lưu trước khi xử lý message hoàn tất nhưng không có ACK thành công; lỗi validation hoặc lỗi SQLite trả ERROR. Status monitor chỉ cập nhật cột `agents.status`; Server hiện chưa ghi event vào bảng `events`.

## Các kịch bản được kiểm thử

Test tích hợp [`tests/test_database_server_integration.py`](../tests/test_database_server_integration.py) dùng TCP loopback thật và SQLite tạm, bao gồm:

- REGISTER, HEARTBEAT, METRICS và truy vấn lại dữ liệu.
- Tuổi tín hiệu ở 10, 11, 15, 16 giây; Heartbeat đưa status về ONLINE.
- REGISTER chưa có Heartbeat, reconnect cùng `agent_id`, và Heartbeat từ socket cũ.
- Session stale/REGISTER lặp, disconnect cleanup, frame phân mảnh/nối tiếp, lỗi DB và shutdown threads.
