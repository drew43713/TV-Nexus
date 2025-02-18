import sqlite3
from fastapi import HTTPException
from .config import DB_FILE

def init_db():
    """
    Initialize or upgrade the database schema:
      - Creates/updates the 'channels' table (with channel_number).
      - Creates/updates the 'epg_programs' and 'epg_channels' tables.
      - Creates/updates the 'raw_epg_channels' and 'raw_epg_programs' tables,
        including the new 'raw_epg_file' column in raw_epg_programs.
    """
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # Create channels table if it does not exist.
    c.execute('''
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY,
            name TEXT,
            url TEXT,
            tvg_name TEXT,
            original_tvg_name TEXT,
            logo_url TEXT,
            group_title TEXT,
            active INTEGER DEFAULT 0
        )
    ''')

    # Check if 'channel_number' column exists.
    c.execute("PRAGMA table_info(channels)")
    columns = [col_info[1] for col_info in c.fetchall()]
    if "channel_number" not in columns:
        try:
            c.execute("ALTER TABLE channels ADD COLUMN channel_number INTEGER")
        except sqlite3.OperationalError as e:
            print("Error adding channel_number column:", e)

    # Create a unique index on channel_number (if needed).
    try:
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_channel_number ON channels(channel_number)")
    except sqlite3.OperationalError as e:
        print("Error creating unique index for channel_number:", e)

    # For existing records where channel_number is NULL, set it equal to id.
    try:
        c.execute("UPDATE channels SET channel_number = id WHERE channel_number IS NULL")
    except sqlite3.OperationalError as e:
        print("Error updating channel_number:", e)

    # Create epg_programs table.
    c.execute('''
        CREATE TABLE IF NOT EXISTS epg_programs (
            id INTEGER PRIMARY KEY,
            channel_tvg_name TEXT,
            start DATETIME,
            stop DATETIME,
            title TEXT,
            description TEXT
        )
    ''')

    # Create epg_channels table.
    c.execute('''
        CREATE TABLE IF NOT EXISTS epg_channels (
            name TEXT PRIMARY KEY
        )
    ''')

    # Create raw_epg_channels table.
    c.execute('''
        CREATE TABLE IF NOT EXISTS raw_epg_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_id TEXT,
            display_name TEXT
        )
    ''')

    # Create raw_epg_programs table with the new raw_epg_file column.
    c.execute('''
        CREATE TABLE IF NOT EXISTS raw_epg_programs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_channel_id TEXT,
            start TEXT,
            stop TEXT,
            title TEXT,
            description TEXT,
            icon_url TEXT,
            raw_epg_file TEXT
        )
    ''')

    conn.commit()
    conn.close()

def swap_channel_numbers(current_number: int, new_number: int) -> bool:
    """
    Swap channel numbers if new_number is already in use.
    Otherwise, simply update the record.
    Returns True if a swap occurred.
    """
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # Confirm channel with current_number exists.
    c.execute("SELECT id FROM channels WHERE channel_number = ?", (current_number,))
    if not c.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Channel with current number not found.")

    # Check if new_number is already in use.
    c.execute("SELECT id FROM channels WHERE channel_number = ?", (new_number,))
    row_new = c.fetchone()
    swap = (row_new is not None)

    if swap:
        # Use a temporary number to perform the swap.
        temp_number = -1
        c.execute("SELECT id FROM channels WHERE channel_number = ?", (temp_number,))
        if c.fetchone():
            temp_number = -abs(current_number + new_number)
        c.execute("UPDATE channels SET channel_number = ? WHERE channel_number = ?", (temp_number, current_number))
        c.execute("UPDATE channels SET channel_number = ? WHERE channel_number = ?", (current_number, new_number))
        c.execute("UPDATE channels SET channel_number = ? WHERE channel_number = ?", (new_number, temp_number))
    else:
        c.execute("UPDATE channels SET channel_number = ? WHERE channel_number = ?", (new_number, current_number))

    conn.commit()
    conn.close()
    return swap