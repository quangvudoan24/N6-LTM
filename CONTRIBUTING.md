Quy tắc làm việc với Git – N6-LTM

1. Quy tắc nhánh (Branch)

* main: Chứa phiên bản chính thức của dự án. Không push trực tiếp lên nhánh này.
* Mỗi thành viên làm việc trên một branch riêng.
* Đặt tên branch theo nhiệm vụ, ví dụ:
    * feature/agent-data
    * feature/agent-tcp
    * feature/tcp-server
    * feature/database
    * feature/tests
    * feature/dashboard

2. Quy trình làm việc

Bước 1: Cập nhật code mới nhất

git checkout main
git pull origin main

Bước 2: Chuyển sang branch của mình

git checkout feature/agent-data

Bước 3: Sau khi hoàn thành công việc

git add .
git commit -m "feat: mo ta cong viec"
git push -u origin feature/agent-data

Thay feature/agent-data bằng branch của thành viên tương ứng.

Bước 4: Gửi Pull Request (PR)

Tạo Pull Request trên GitHub để nhóm kiểm tra. Chỉ gộp vào main sau khi được xem xét và chấp thuận.

3. Quy ước commit

Sử dụng các tiền tố sau:

* feat: Thêm chức năng mới.
* fix: Sửa lỗi.
* docs: Cập nhật tài liệu.
* test: Thêm hoặc chỉnh sửa kiểm thử.
* refactor: Tổ chức lại code.

Ví dụ:

git commit -m "docs: update database design"

4. Quy tắc viết code

1. Không hard-code port, IP hoặc tên trạng thái nếu có thể cấu hình riêng.
2. Đặt tên biến và hàm rõ ràng, thống nhất.
3. Không commit các file .env, *.db, __pycache__/ hoặc thông tin bí mật.
4. Kiểm tra code trước khi push.
5. Viết Python theo quy ước PEP 8.

5. Nguyên tắc chung

* Làm đúng nhiệm vụ đã được giao trên GitHub Issues.
* Cập nhật tiến độ khi hoàn thành công việc.
* Không tự ý sửa hoặc xóa code của thành viên khác.
* Trao đổi với nhóm khi thay đổi ảnh hưởng đến các thành phần khác.
