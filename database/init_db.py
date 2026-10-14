"""Tạo file DB từ schema.sql.  Chạy từ thư mục gốc:  python -m database.init_db"""
import sqlite3
from pathlib import Path
from common.constants import DB_PATH

def init_db(db_path=DB_PATH):
    schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(schema)
    conn.commit()
    conn.close()
    print(f"Đã tạo DB tại {db_path}")

if __name__ == "__main__":
    init_db()
