Phần phụ trách ch: triển khai TCP server
1. Mục tiêu và phạm vi công việc
Mục tiêu của phần việc này là xây dựng TCP Server tiếp nhận và xử lý dữ liệu giám sát do các Agent trên máy Client gửi lên, trong hệ thống giám sát mạng của nhóm.
Các yêu cầu đã thực hiện:
Tạo Server sử dụng giao thức TCP, lắng nghe trên cổng 9000 theo cấu hình dùng chung (common/constants.py).
Chấp nhận kết nối từ nhiều Agent.
Nhận bản tin JSON, kiểm tra cấu trúc và giá trị trước khi xử lý.
Gửi phản hồi ACK hoặc ERROR cho Agent sau khi tiếp nhận bản tin.
Xử lý các trường hợp Agent mất kết nối, dữ liệu không hợp lệ và lỗi socket mà không làm dừng Server.
Chuẩn bị giao diện để lưu dữ liệu vào SQLite.
Các file liên quan: server/server.py (Server), tests/test_server.py (kiểm thử), docs/PROTOCOL.md (tài liệu giao thức).
2. Những gì đã làm và thiết kết
Tổng quan cấu trúc: Toàn bộ phần Server nằm trong file server/server.py, chia thành ba nhóm chức năng:
•	Nhóm socket: create_server_socket() tạo socket lắng nghe, serve_forever() chấp nhận kết nối mới, handle_client() phục vụ từng Agent.
•	Nhóm xử lý bản tin: handle_line(), process_message() và check_values() giải mã, kiểm tra và quyết định phản hồi.
•	Nhóm lưu trữ: lớp NullStorage và hàm load_storage() làm cầu nối với tầng cơ sở dữ liệu.
Tách như vậy giúp phần mạng, phần kiểm tra dữ liệu và phần lưu trữ độc lập nhau. Nhờ đó có thể kiểm thử từng phần và thay phần lưu trữ mà không phải sửa phần mạng.
Khởi tạo và kết nối. Server tạo socket TCP, lắng nghe tại SERVER_HOST:SERVER_PORT (9000) lấy từ common/constants.py. Vòng lặp chấp nhận kết nối có timeout 1 giây để nhận được lệnh dừng Ctrl+C, kể cả trên Windows. Nếu cổng bị chiếm, Server ghi một dòng log lỗi rồi thoát. Trên Windows, Server dùng tùy chọn cổng độc quyền để hai Server không cùng chiếm một cổng.
Phục vụ nhiều Agent đồng thời. Mỗi kết nối được giao cho một thread riêng chạy handle_client(). Một Agent chậm hoặc bị treo chỉ giữ thread của nó, không làm chậm Agent khác. Cách này phù hợp với số lượng Agent nhỏ.
Đọc bản tin theo ký tự xuống dòng. TCP là luồng byte nên một lần nhận có thể chứa nửa bản tin hoặc nhiều bản tin liền nhau. Server giữ dữ liệu chưa đủ trong bộ đệm của từng kết nối, cắt từng dòng theo \n và giải mã riêng từng dòng bằng decode() của module giao thức chung. Nhờ vậy một dòng hỏng chỉ nhận ERROR, các dòng hợp lệ cùng gói vẫn được xử lý.
Kiểm tra bản tin. Ngoài việc decode() kiểm tra JSON, loại bản tin và các trường bắt buộc, Server kiểm tra thêm:
•	Chỉ nhận REGISTER, HEARTBEAT, METRICS từ Agent.
•	agent_id là chuỗi không rỗng; REGISTER có hostname, ip, os hợp lệ.
•	timestamp là số nguyên không âm.
•	cpu, ram, disk là số trong khoảng 0 đến 100 (loại giá trị chữ, logic và NaN).
Quy trình theo từng kết nối. Agent phải gửi REGISTER trước, lúc đó Server ghi nhớ agent_id cho kết nối. HEARTBEAT và METRICS bị từ chối nếu chưa REGISTER hoặc dùng agent_id khác. Thời điểm nhận tin gần nhất được ghi theo đồng hồ của Server để không bị sai lệch khi đồng hồ Agent chạy lệch.
Phản hồi cho Agent. Bản tin hợp lệ nhận ACK; bản tin sai nhận ERROR kèm lý do và kết nối vẫn được giữ. Việc ACK nay là phản hồi cho mọi bản tin hợp lệ (trước đây chỉ ghi cho REGISTER) đã được cập nhật vào docs/PROTOCOL.md.
Xử lý lỗi và mất kết nối. Mọi lỗi chỉ ảnh hưởng đúng kết nối gây ra lỗi, Server vẫn chạy:
Agent ngắt kết nối bình thường hoặc đột ngột: ghi log và đóng kết nối.
Agent im lặng quá 30 giây: đóng kết nối (lớn hơn nhiều so với chu kỳ HEARTBEAT 5 giây).
Bản tin dài quá 64KB mà chưa có \n: báo ERROR rồi ngắt, tránh tràn bộ nhớ.
Lỗi khi lưu dữ liệu: ghi log đầy đủ, trả ERROR ngắn gọn, giữ kết nối.

Ghi log. Server dùng module logging: mức thông tin cho kết nối, đăng ký và ngắt kết nối; mức cảnh báo cho bản tin sai và Agent im lặng; mức lỗi cho lỗi socket và lỗi lưu dữ liệu. Mỗi dòng có thời gian và tên thread.
Giao diện lưu SQLite. Server gọi ba hàm trong database/db.py: save_agent (khi REGISTER), save_metrics (khi METRICS) và touch_agent (cập nhật thời điểm nhận tin). Nếu module chưa có, Server dùng NullStorage chỉ ghi log; khi module có đủ ba hàm, Server tự dùng mà không cần sửa mã. Vì mỗi Agent chạy trên một thread, mỗi hàm lưu trữ cần dùng kết nối SQLite riêng hoặc một khóa bảo vệ.
Cài đặt riêng của Server. Bốn thông số LISTEN_BACKLOG (5), ACCEPT_TIMEOUT (1 giây), CLIENT_TIMEOUT (30 giây) và MAX_MESSAGE_SIZE (64KB) đặt ở đầu server/server.py, không sửa file cấu hình chung và không ảnh hưởng đến Agent hay TCP client. monitor_loop() vẫn để nguyên, chưa triển khai.
3. Kiểm thử và kết quả
Kiểm thử tự động. Bộ kiểm thử tests/test_server.py dùng socket thật trên cổng ngẫu nhiên (không dùng cổng 9000) và lớp lưu trữ giả để kiểm tra dữ liệu Server chuyển đi. Các tình huống đã kiểm tra:
•	Nhận ACK khi gửi REGISTER, HEARTBEAT, METRICS hợp lệ; dữ liệu được chuyển đúng sang lớp lưu trữ.
•	Nhận ERROR khi gửi JSON hỏng, thiếu trường, loại bản tin lạ, bản tin không phải object, trường type sai kiểu, bản tin sai chiều, giá trị cpu ngoài khoảng, giá trị chữ, NaN, timestamp sai kiểu, agent_id không khớp.
•	Gửi dữ liệu khi chưa REGISTER bị từ chối.
•	Hai bản tin trong một gói, một bản tin bị tách đôi, và một dòng hỏng nằm giữa các dòng tốt đều được xử lý đúng.
•	Agent ngắt đột ngột giữa chừng không làm sập Server; Server vẫn nhận Agent khác.
•	Nhiều Agent kết nối đồng thời.
•	Lỗi lưu dữ liệu trả ERROR, kết nối vẫn dùng được sau đó.
•	Bản tin quá dài bị ngắt, Server vẫn phục vụ Agent khác.
•	Cổng đã bị chiếm gây lỗi khi khởi động Server thứ hai.
Kết quả: 27/27 test đạt (gồm 5 test giao thức có sẵn), chạy trên Windows với Python 3.14.
Kiểm thử thực tế. Khởi động Server bằng python -m server.server, dùng một client giả lập Agent gửi lần lượt REGISTER, HEARTBEAT, METRICS và một dòng không phải JSON. Client nhận 3 phản hồi ACK và 1 phản hồi ERROR, trong khi Server ghi log kết nối, đăng ký Agent và cảnh báo bản tin không hợp lệ. Ảnh chụp minh chứng được đính kèm trong Pull Request.
Sản phẩm bàn giao. Mã nguồn được đưa lên nhánh feature/tcp-server và gửi Pull Request vào nhánh main để nhóm review.
4. Hạn chế và những gì chưa làm hoàn thiện
Dữ liệu chưa được lưu thật vào SQLite, Server đang dùng NullStorage chỉ ghi log.
Luồng giám sát monitor_loop() (phát hiện Agent im lặng để chuyển trạng thái WARNING, OFFLINE) chưa được triển khai.
Chưa kiểm thử với chương trình Agent thật (agent/agent.py), nên chưa xác nhận Agent đọc ACK sau mỗi lần gửi.
Chưa có dữ liệu Server cung cấp cho Dashboard.
