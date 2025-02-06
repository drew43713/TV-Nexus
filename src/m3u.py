import os
import sqlite3
import html
from .config import M3U_DIR, DB_FILE
from .epg import parse_epg_files

def parse_m3u_attribute(line: str, attr_name: str) -> str:
    lower_line = line.lower()
    key = f'{attr_name.lower()}="'
    start = lower_line.find(key)
    if start == -1:
        return ""
    start += len(key)
    end = lower_line.find('"', start)
    if end == -1:
        return ""
    # Unescape any HTML/XML entities in the attribute value.
    return html.unescape(line[start:end])

def find_m3u_files():
    return [
        os.path.join(M3U_DIR, f)
        for f in os.listdir(M3U_DIR)
        if f.endswith(".m3u")
    ]

def load_m3u_files():
    m3u_files = find_m3u_files()
    if not m3u_files:
        print("[INFO] No M3U files found.")
        return

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    for m3u_file in m3u_files:
        print(f"[INFO] Loading M3U: {m3u_file}")
        with open(m3u_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        idx = 0
        while idx < len(lines):
            line = lines[idx].strip()
            if line.startswith("#EXTINF"):
                # Extract the channel name part (after the comma)
                name_part = line.split(",", 1)[-1].strip()
                # Parse attributes from the line.
                tvg_name = parse_m3u_attribute(line, "tvg-name")
                tvg_logo = parse_m3u_attribute(line, "tvg-logo")
                group_title = parse_m3u_attribute(line, "group-title")
                if (idx + 1) < len(lines):
                    url = lines[idx + 1].strip()
                else:
                    url = ""

                # Use original tvg_name if available; otherwise fall back to channel name.
                key = tvg_name if tvg_name else name_part

                # Look up the channel using the original_tvg_name.
                c.execute("SELECT id, url FROM channels WHERE original_tvg_name = ?", (key,))
                row = c.fetchone()
                if row:
                    channel_id, old_url = row
                    if old_url != url:
                        # Only update the URL.
                        c.execute("UPDATE channels SET url = ? WHERE id = ?", (url, channel_id))
                        print(f"[INFO] Updated channel '{key}' with a new URL.")
                else:
                    c.execute("""
                        INSERT INTO channels (name, url, tvg_name, original_tvg_name, logo_url, group_title)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (name_part, url, tvg_name, tvg_name, tvg_logo, group_title))
                    print(f"[INFO] Inserted new channel '{key}'.")
                idx += 2
            else:
                idx += 1

    conn.commit()
    conn.close()

    print("[INFO] Channels updated. Updating modified EPG file...")
    parse_epg_files()