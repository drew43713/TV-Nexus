import os
import sqlite3
import html
import hashlib
import requests
import re
from .config import M3U_DIR, DB_FILE, LOGOS_DIR
from .epg import parse_raw_epg_files, build_combined_epg

def cache_logo(logo_url: str, channel_identifier: str = None) -> str:
    if not logo_url:
        return logo_url
    try:
        logos_dir = os.path.abspath(LOGOS_DIR)
        os.makedirs(logos_dir, exist_ok=True)
        ext = os.path.splitext(logo_url)[1]
        if not ext or len(ext) > 5:
            ext = ".jpg"
        if channel_identifier and channel_identifier.strip():
            sanitized = re.sub(r'[^\w\-]+', '_', channel_identifier.strip().lower())
            if sanitized:
                h = hashlib.md5(logo_url.encode("utf-8")).hexdigest()[:8]
                filename = f"{sanitized}_{h}{ext}"
            else:
                h = hashlib.md5(logo_url.encode("utf-8")).hexdigest()
                filename = f"{h}{ext}"
        else:
            h = hashlib.md5(logo_url.encode("utf-8")).hexdigest()
            filename = f"{h}{ext}"
        filepath = os.path.join(logos_dir, filename)
        if os.path.exists(filepath):
            if os.path.getsize(filepath) > 0:
                return f"/static/logos/{filename}"
            else:
                os.remove(filepath)
        response = requests.get(logo_url, timeout=10)
        if response.status_code == 200:
            with open(filepath, "wb") as f:
                f.write(response.content)
        else:
            print(f"Warning: Failed to download logo {logo_url} (status: {response.status_code}).")
            return logo_url
        return f"/static/logos/{filename}"
    except Exception as e:
        print(f"Error caching logo {logo_url}: {e}")
        return logo_url

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
    return html.unescape(line[start:end])

def find_m3u_file():
    """Return the first found M3U file from the M3U directory."""
    os.makedirs(M3U_DIR, exist_ok=True)
    for f in os.listdir(M3U_DIR):
        if f.lower().endswith(".m3u"):
            return os.path.join(M3U_DIR, f)
    return None

def load_m3u_files():
    # Process only one M3U file at a time.
    m3u_file = find_m3u_file()
    if not m3u_file:
        print(f"[INFO] No M3U file found. Please upload an M3U file to the {M3U_DIR} directory and restart the app.")
        return

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # Ensure that the channels table has a 'removed_reason' column.
    c.execute("PRAGMA table_info(channels)")
    columns = [col_info[1] for col_info in c.fetchall()]
    if "removed_reason" not in columns:
        try:
            c.execute("ALTER TABLE channels ADD COLUMN removed_reason TEXT")
            print("[INFO] Added removed_reason column to channels.")
        except Exception as e:
            print("Warning: Could not add removed_reason column:", e)

    print(f"[INFO] Loading M3U: {m3u_file}")
    with open(m3u_file, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # Collect keys using the channel name (name_part) from the M3U file.
    m3u_keys = set()

    idx = 0
    while idx < len(lines):
        line = lines[idx].strip()
        if line.startswith("#EXTINF"):
            name_part = line.split(",", 1)[-1].strip()
            tvg_name = parse_m3u_attribute(line, "tvg-name")
            tvg_logo = parse_m3u_attribute(line, "tvg-logo")
            group_title = parse_m3u_attribute(line, "group-title")
            url = lines[idx + 1].strip() if (idx + 1) < len(lines) else ""

            remote_logo = tvg_logo if tvg_logo else ""
            # For cleanup purposes, add the channel's name to the set.
            m3u_keys.add(name_part)

            # Use tvg_name if available; otherwise, fallback to name_part for lookup.
            key = tvg_name if tvg_name else name_part
            # Fetch removed_reason along with other fields.
            c.execute("SELECT id, url, logo_url, removed_reason FROM channels WHERE tvg_name = ? OR name = ?", (key, key))
            row = c.fetchone()
            if row:
                channel_id, old_url, old_logo, removed_reason = row
                default_logo = cache_logo(remote_logo, channel_identifier=key) if remote_logo else ""
                tvg_logo_local = old_logo if old_logo and old_logo != default_logo else default_logo
                if removed_reason:
                    # Channel was previously removed; update details but do not change active status.
                    c.execute("""
                        UPDATE channels 
                        SET url = ?, logo_url = ?, name = ?, group_title = ?
                        WHERE id = ?
                    """, (url, tvg_logo_local, name_part, group_title, channel_id))
                    print(f"[INFO] Updated channel '{key}' details but left as removed (removed_reason: {removed_reason}).")
                else:
                    if old_url != url or (old_logo != tvg_logo_local):
                        # Update channel details without modifying the active status.
                        c.execute("""
                            UPDATE channels 
                            SET url = ?, logo_url = ?, name = ?, group_title = ?, removed_reason = NULL
                            WHERE id = ?
                        """, (url, tvg_logo_local, name_part, group_title, channel_id))
                        print(f"[INFO] Updated channel '{key}' with new URL and logo (active status unchanged).")
                    else:
                        # No changes necessary; leave active status as is.
                        print(f"[INFO] Channel '{key}' already up-to-date; active status unchanged.")
            else:
                tvg_logo_local = cache_logo(remote_logo, channel_identifier=key) if remote_logo else ""
                # Insert new channel as inactive (active = 0)
                c.execute("""
                    INSERT INTO channels (name, url, tvg_name, logo_url, group_title, active)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (name_part, url, tvg_name, tvg_logo_local, group_title, 0))
                new_id = c.lastrowid
                # Assign channel_number to the next available positive integer not currently used.
                c.execute("SELECT channel_number FROM channels WHERE channel_number IS NOT NULL ORDER BY channel_number ASC")
                used_numbers = [row[0] for row in c.fetchall() if isinstance(row[0], int)]
                next_number = 1
                for n in used_numbers:
                    if n == next_number:
                        next_number += 1
                    elif n > next_number:
                        break
                c.execute("UPDATE channels SET channel_number = ? WHERE id = ?", (next_number, new_id))
                print(f"[INFO] Inserted new channel '{key}' with logo. (Inactive by default, channel_number set to {next_number})")
            idx += 2
        else:
            idx += 1

    # Perform cleanup: mark any channels that are active in the database
    # but whose 'name' is not present in the current M3U file as inactive.
    c.execute("SELECT id, name, active FROM channels")
    for channel in c.fetchall():
        chan_id, chan_name, active = channel
        if chan_name not in m3u_keys and active == 1:
            c.execute("UPDATE channels SET active = 0, removed_reason = 'Removed from M3U' WHERE id = ?", (chan_id,))
            print(f"[INFO] Marked channel '{chan_name}' as removed (not in current M3U).")

    conn.commit()
    conn.close()

    print("[INFO] Channels updated. Updating modified EPG file...")
    parse_raw_epg_files()
    build_combined_epg()
