"""
AGENT - TV2 (Hải Huỳnh) lo kết nối/reconnect, TV3 (Thành Công) lo heartbeat.
Đây chỉ là khung. Mọi người code tiếp vào đúng TODO của mình.
"""
import socket
import platform
import time

from common.constants import DEFAULT_SERVER_ADDR, SERVER_PORT, HEARTBEAT_INTERVAL, RECONNECT_DELAYS
from common.protocol import encode, make_register, make_heartbeat, make_metrics


def connect_with_retry(host, port):
    """TODO (TV2): thử kết nối, thất bại thì chờ theo RECONNECT_DELAYS."""
    pass


def heartbeat_loop(sock, agent_id):
    """Gửi heartbeat ngay sau khi được gọi, rồi gửi lại theo chu kỳ cấu hình.

    Lỗi socket được để truyền lên tầng gọi để phần kết nối/reconnect của TV2 xử lý.
    """
    while True:
        sock.sendall(encode(make_heartbeat(agent_id)))
        time.sleep(HEARTBEAT_INTERVAL)


def collect_metrics():
    """TODO: dùng psutil lấy cpu_percent, virtual_memory().percent, disk_usage('/').percent."""
    pass


def main():
    print(f"Agent chạy trên {platform.node()} ({platform.system()})")
    # TODO: connect -> gửi REGISTER -> chờ ACK -> chạy heartbeat + metrics


if __name__ == "__main__":
    main()
