"""
HẰNG SỐ DÙNG CHUNG - NGUỒN SỰ THẬT DUY NHẤT (single source of truth).
Quy tắc: KHÔNG hard-code các số/chuỗi này ở nơi khác. Cần đổi -> sửa ở đây + báo nhóm trưởng.
"""

# ---- Mạng ----
SERVER_HOST = "0.0.0.0"       # Server lắng nghe mọi card mạng
DEFAULT_SERVER_ADDR = "127.0.0.1"  # Agent mặc định kết nối tới (đổi qua .env / tham số)
SERVER_PORT = 9000
BUFFER_SIZE = 4096
ENCODING = "utf-8"
MESSAGE_DELIMITER = b"\n"     # Mỗi tin nhắn JSON kết thúc bằng \n

# ---- Heartbeat / trạng thái (đơn vị: giây) ----
HEARTBEAT_INTERVAL = 5        # Agent gửi HEARTBEAT mỗi 5s
METRICS_INTERVAL = 5          # Agent gửi METRICS mỗi 5s
WARNING_AFTER = 10            # >10s không có tín hiệu -> WARNING
OFFLINE_AFTER = 15            # >15s không có tín hiệu -> OFFLINE
MONITOR_SCAN_INTERVAL = 5     # Luồng giám sát của Server quét mỗi 5s
RECONNECT_DELAYS = [3, 5, 10] # Agent thử kết nối lại: 3s, 5s, 10s rồi giữ 10s

# ---- Dashboard / DB ----
DASHBOARD_REFRESH = 3         # Làm mới giao diện mỗi 3s
METRICS_RETENTION_DAYS = 7    # Xóa metrics cũ hơn 7 ngày
DB_PATH = "database/monitor.db"

# ---- Ngưỡng cảnh báo (%) ----
CPU_ALERT_THRESHOLD = 90
RAM_ALERT_THRESHOLD = 90
DISK_ALERT_THRESHOLD = 90


class MsgType:
    REGISTER = "REGISTER"
    HEARTBEAT = "HEARTBEAT"
    METRICS = "METRICS"
    ACK = "ACK"
    ERROR = "ERROR"


class Status:
    ONLINE = "ONLINE"
    WARNING = "WARNING"
    OFFLINE = "OFFLINE"


class EventType:
    WENT_ONLINE = "WENT_ONLINE"
    WENT_WARNING = "WENT_WARNING"
    WENT_OFFLINE = "WENT_OFFLINE"
    HIGH_CPU = "HIGH_CPU"
    HIGH_RAM = "HIGH_RAM"
    HIGH_DISK = "HIGH_DISK"
