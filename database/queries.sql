-- Truy vấn mẫu cho Dashboard (TV6 duy trì). Tham số dùng ? (placeholder), KHÔNG nối chuỗi.

-- 1. Danh sách máy kèm trạng thái
SELECT agent_id, hostname, ip_address, status, last_seen FROM agents ORDER BY hostname;

-- 2. 50 số liệu gần nhất của một máy (tham số: agent_id)
SELECT cpu, ram, disk, timestamp FROM metrics
WHERE agent_id = ? ORDER BY timestamp DESC LIMIT 50;

-- 3. Đếm máy theo trạng thái
SELECT status, COUNT(*) AS total FROM agents GROUP BY status;

-- 4. 20 sự kiện gần nhất
SELECT agent_id, event_type, message, timestamp FROM events ORDER BY timestamp DESC LIMIT 20;

-- 5. Dọn metrics cũ (tham số: mốc timestamp = now - 7 ngày)
DELETE FROM metrics WHERE timestamp < ?;
