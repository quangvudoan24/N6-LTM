"""
SERVER - TV1 (Dũng) lo phần socket, TV4 (Thanh Bình) lo phần giám sát heartbeat,
TV6 (Gia Đạt) cung cấp hàm ghi DB.
Đây chỉ là khung. Mọi người code tiếp vào đúng TODO của mình.
"""
from common.constants import SERVER_HOST, SERVER_PORT, BUFFER_SIZE
from common.protocol import LineBuffer, encode, make_ack, ProtocolError


def handle_client(conn, addr):
    """Mỗi client chạy trong 1 thread riêng."""
    # TODO (TV1): vòng lặp recv -> LineBuffer.feed -> xử lý từng tin nhắn
    # TODO (TV1): xử lý ngắt kết nối (ConnectionResetError, recv trả b"")
    pass


def monitor_loop():
    """Luồng giám sát: mỗi MONITOR_SCAN_INTERVAL giây kiểm tra last_seen."""
    # TODO (TV4): tính WARNING/OFFLINE theo constants, ghi event
    pass


def main():
    # TODO (TV1): socket -> bind -> listen -> accept -> threading.Thread(handle_client)
    # TODO (TV4): khởi động threading.Thread(monitor_loop, daemon=True)
    print(f"Server sẽ chạy tại {SERVER_HOST}:{SERVER_PORT}")


if __name__ == "__main__":
    main()
