import sqlite3
from typing import Optional

from .config import DB_FILE


def get_conn(db_file: Optional[str] = None) -> sqlite3.Connection:
    """Return a SQLite connection with sane defaults for concurrent reads/writes.

    - WAL mode reduces writer blocking readers.
    - busy_timeout helps when concurrent writes happen (e.g., parsing + requests).
    """
    path = db_file or DB_FILE
    conn = sqlite3.connect(path)

    # Pragmas for better concurrency.
    # Note: journal_mode returns the new journal mode as a result row.
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")

    return conn
