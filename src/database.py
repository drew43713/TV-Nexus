import sqlite3
from fastapi import HTTPException
import os
from .config import DB_FILE

def init_db():
    """
    Initialize or upgrade the database schema:
      - Create or update the 'channels' table (with an 'active' column added).
      - Create/upgrade 'epg_programs', 'epg_channels', 'raw_epg_channels' and 'raw_epg_programs' tables.
    """
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # Create channels table with a new 'active' column (0 = inactive, 1 = active)
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
    # For existing tables, try adding the column if it doesn’t exist.
    try:
        c.execute("ALTER TABLE channels ADD COLUMN active INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

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

    # Create raw_epg_programs table.
    c.execute('''
        CREATE TABLE IF NOT EXISTS raw_epg_programs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_channel_id TEXT,
            start TEXT,
            stop TEXT,
            title TEXT,
            description TEXT
        )
    ''')

    conn.commit()
    conn.close()

def swap_channel_ids(current_id: int, new_id: int) -> bool:
    """
    Swap channel IDs if new_id is already in use.
    Also update the epg_programs table accordingly.
    """
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # Confirm current channel exists
    c.execute("SELECT id FROM channels WHERE id = ?", (current_id,))
    if not c.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Current channel not found.")

    # Check if new_id already exists
    c.execute("SELECT id FROM channels WHERE id = ?", (new_id,))
    row_new = c.fetchone()
    swap = (row_new is not None)

    if swap:
        # Use a temporary id to swap
        temp_id = -1
        c.execute("SELECT id FROM channels WHERE id = ?", (temp_id,))
        if c.fetchone():
            temp_id = -abs(current_id + new_id)
        c.execute("UPDATE channels SET id = ? WHERE id = ?", (temp_id, current_id))
        c.execute("UPDATE channels SET id = ? WHERE id = ?", (current_id, new_id))
        c.execute("UPDATE channels SET id = ? WHERE id = ?", (new_id, temp_id))

        # Update epg_programs references
        c.execute("""
            UPDATE epg_programs 
            SET channel_tvg_name = ? 
            WHERE channel_tvg_name = ?
        """, (str(temp_id), str(current_id)))
        c.execute("""
            UPDATE epg_programs 
            SET channel_tvg_name = ? 
            WHERE channel_tvg_name = ?
        """, (str(current_id), str(new_id)))
        c.execute("""
            UPDATE epg_programs 
            SET channel_tvg_name = ? 
            WHERE channel_tvg_name = ?
        """, (str(new_id), str(temp_id)))
    else:
        c.execute("UPDATE channels SET id = ? WHERE id = ?", (new_id, current_id))
        c.execute("""
            UPDATE epg_programs 
            SET channel_tvg_name = ? 
            WHERE channel_tvg_name = ?
        """, (str(new_id), str(current_id)))

    conn.commit()
    conn.close()
    return swap
