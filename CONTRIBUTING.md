# Quy tắc làm việc với Git

## Nhánh (branch)
- `main`: luôn chạy được, **cấm push trực tiếp**.
- Mỗi người làm trên nhánh riêng: `feature/<tên>-<việc>`
  - Ví dụ: `feature/dung-tcp-server`, `feature/vu-dashboard-wireframe`

## Quy trình mỗi ngày
```bash
git checkout main && git pull            # 1. lấy code mới nhất
git checkout -b feature/ten-viec         # 2. tạo nhánh (lần đầu)
# ... làm việc ...
git add .
git commit -m "feat(server): them ham accept nhieu client"
git push -u origin feature/ten-viec      # 3. đẩy lên
# 4. Lên GitHub/GitLab tạo Pull Request -> nhóm trưởng duyệt -> merge
```

## Quy ước commit
`<loại>(<phạm vi>): <mô tả ngắn>`
- Loại: `feat` (tính năng), `fix` (sửa lỗi), `docs` (tài liệu), `test`, `refactor`
- Phạm vi: `server`, `agent`, `db`, `dashboard`, `common`, `docs`

## Quy tắc vàng
1. Không hard-code port/timeout/tên trạng thái -> dùng `common/constants.py`.
2. Không tự đổi định dạng tin nhắn / schema -> sửa tài liệu + Pull Request + báo nhóm.
3. Không commit `.env`, `*.db`, `__pycache__`.
4. Commit nhỏ, thường xuyên; trước khi push chạy `pytest`.
5. Code Python theo PEP8: tên hàm/biến `snake_case`, hằng số `UPPER_CASE`, class `PascalCase`.
