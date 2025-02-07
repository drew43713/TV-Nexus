import sqlite3
from fastapi import HTTPException
from .config import DB_FILE

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # Create the channels table with the new original_tvg_name column.
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

    # In case the table already existed, try to add new columns if they do not exist.
    try:
        c.execute('ALTER TABLE channels ADD COLUMN logo_url TEXT')
    except sqlite3.OperationalError:
        pass
    try:
        c.execute('ALTER TABLE channels ADD COLUMN group_title TEXT')
    except sqlite3.OperationalError:
        pass
    try:
        c.execute('ALTER TABLE channels ADD COLUMN original_tvg_name TEXT')
    except sqlite3.OperationalError:
        pass

    # Create EPG table (unchanged)
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
    conn.commit()
    conn.close()

def swap_channel_ids(current_id: int, new_id: int) -> bool:
    """
    Update the channel's id in the database.
    If new_id is already used, swap the two channel ids.
    Returns True if a swap occurred, else False.
    """
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id FROM channels WHERE id = ?", (current_id,))
    if not c.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Current channel not found.")
    c.execute("SELECT id FROM channels WHERE id = ?", (new_id,))
    row_new = c.fetchone()
    swap = (row_new is not None)
    if swap:
        temp_id = -1
        c.execute("SELECT id FROM channels WHERE id = ?", (temp_id,))
        if c.fetchone():
            temp_id = -abs(current_id + new_id)
        c.execute("UPDATE channels SET id = ? WHERE id = ?", (temp_id, current_id))
        c.execute("UPDATE channels SET id = ? WHERE id = ?", (current_id, new_id))
        c.execute("UPDATE channels SET id = ? WHERE id = ?", (new_id, temp_id))
        c.execute("UPDATE epg_programs SET channel_tvg_name = ? WHERE channel_tvg_name = ?", (str(temp_id), str(current_id)))
        c.execute("UPDATE epg_programs SET channel_tvg_name = ? WHERE channel_tvg_name = ?", (str(current_id), str(new_id)))
        c.execute("UPDATE epg_programs SET channel_tvg_name = ? WHERE channel_tvg_name = ?", (str(new_id), str(temp_id)))
    else:
        c.execute("UPDATE channels SET id = ? WHERE id = ?", (new_id, current_id))
        c.execute("UPDATE epg_programs SET channel_tvg_name = ? WHERE channel_tvg_name = ?", (str(new_id), str(current_id)))
    conn.commit()
    conn.close()
    return swap