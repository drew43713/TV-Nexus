import sqlite3
from fastapi import HTTPException
import os

# Adjust or import your DB_FILE path from config as needed.
# Example:
from .config import DB_FILE

def init_db():
    """
    Initialize or upgrade the database schema:
      1. Create/upgrade the 'channels' table (with columns: tvg_name, original_tvg_name, logo_url, group_title).
      2. Create/upgrade 'epg_programs' table for final/merged EPG data.
      3. Create/upgrade 'epg_channels' table for storing distinct final channel display names.
      4. Create 'raw_epg_channels' and 'raw_epg_programs' for storing raw EPG data.
    """
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # -------------------------
    # 1) Channels table
    # -------------------------
    c.execute('''
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY,
            name TEXT,
            url TEXT,
            tvg_name TEXT,
            original_tvg_name TEXT,
            logo_url TEXT,
            group_title TEXT
        )
    ''')
    # Attempt to add missing columns (in case the table existed before).
    try:
        c.execute("ALTER TABLE channels ADD COLUMN logo_url TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE channels ADD COLUMN group_title TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE channels ADD COLUMN original_tvg_name TEXT")
    except sqlite3.OperationalError:
        pass

    # -------------------------
    # 2) Final/Merged EPG table
    # -------------------------
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

    # -------------------------
    # 3) Distinct final EPG channels
    # -------------------------
    c.execute('''
        CREATE TABLE IF NOT EXISTS epg_channels (
            name TEXT PRIMARY KEY
        )
    ''')

    # -------------------------
    # 4) Raw EPG tables (new approach)
    # -------------------------
    # raw_epg_channels holds <channel> info direct from EPG files.
    c.execute('''
        CREATE TABLE IF NOT EXISTS raw_epg_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_id TEXT,
            display_name TEXT
        )
    ''')
    # raw_epg_programs holds <programme> info direct from EPG files.
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
    Update the channel's ID in the database.
    If 'new_id' is already used by another channel, swap the two IDs.

    Returns True if a swap occurred, else False.
    """
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # Confirm the old channel exists
    c.execute("SELECT id FROM channels WHERE id = ?", (current_id,))
    if not c.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Current channel not found.")

    # Check if new_id already exists
    c.execute("SELECT id FROM channels WHERE id = ?", (new_id,))
    row_new = c.fetchone()
    swap = (row_new is not None)

    if swap:
        # We have to swap IDs
        temp_id = -1
        # Ensure temp_id doesn't collide with an existing channel
        c.execute("SELECT id FROM channels WHERE id = ?", (temp_id,))
        if c.fetchone():
            # Use some negative, random-like ID that won't collide
            temp_id = -abs(current_id + new_id)

        # Perform the swap using a temporary ID in the 'channels' table
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
        # No collision, just update the ID
        c.execute("UPDATE channels SET id = ? WHERE id = ?", (new_id, current_id))
        c.execute("""
            UPDATE epg_programs 
            SET channel_tvg_name = ? 
            WHERE channel_tvg_name = ?
        """, (str(new_id), str(current_id)))

    conn.commit()
    conn.close()
    return swap