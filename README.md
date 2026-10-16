N6-LTM – Hệ thống giám sát mạng Client–Server

1. Giới thiệu

Dự án xây dựng hệ thống giám sát thiết bị mạng theo mô hình Client–Server, cho phép thu thập và hiển thị thông tin sử dụng tài nguyên của thiết bị.

2. Chức năng chính

* Thu thập thông tin CPU, RAM và Disk từ Agent.
* Truyền dữ liệu giữa Agent và Server qua TCP.
* Lưu trữ dữ liệu bằng SQLite.
* Hiển thị thông tin giám sát trên Dashboard.

3. Công nghệ sử dụng

* Ngôn ngữ: Python
* Giao tiếp mạng: TCP, JSON
* Cơ sở dữ liệu: SQLite
* Quản lý mã nguồn: GitHub

4. Cấu trúc dự án

* common/: Các thành phần dùng chung
* server/: Xử lý kết nối và dữ liệu
* agent/: Thu thập thông tin thiết bị
* database/: Quản lý cơ sở dữ liệu
* dashboard/: Giao diện giám sát
* docs/: Tài liệu dự án
* design/: Sơ đồ thiết kế
* prototype/: Bản thử nghiệm
* tests/: Kiểm thử hệ thống

5. Thành viên

Dự án được thực hiện bởi nhóm 6 thành viên trong môn Lập trình mạng.

6. Trạng thái

Dự án đang trong giai đoạn thiết kế và phát triển
