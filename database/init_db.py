"""Khởi tạo SQLite từ schema.sql."""
import sqlite3
from pathlib import Path

from common.constants import DB_PATH
from database.operations import DatabaseError


def init_db(db_path=DB_PATH):
    """Tạo các bảng và chỉ mục; có thể chạy lại an toàn."""
    db_path = Path(db_path)
    schema_path = Path(__file__).with_name("schema.sql")

    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        schema = schema_path.read_text(encoding="utf-8")
        connection = sqlite3.connect(db_path)
        try:
            connection.executescript(schema)
            connection.commit()
        finally:
            connection.close()
    except (OSError, sqlite3.Error) as exc:
        raise DatabaseError(f"Không thể khởi tạo SQLite tại {db_path}: {exc}") from exc

    return db_path


if __name__ == "__main__":
    path = init_db()
    print(f"Đã khởi tạo SQLite tại {path}")
