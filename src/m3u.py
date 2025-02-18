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

    print(f"[INFO] Loading M3U: {m3u_file}")
    with open(m3u_file, "r", encoding="utf-8") as f:
        lines = f.readlines()

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
            key = tvg_name if tvg_name else name_part

            c.execute("SELECT id, url, logo_url FROM channels WHERE tvg_name = ? OR name = ?", (key, key))
            row = c.fetchone()
            if row:
                channel_id, old_url, old_logo = row
                default_logo = cache_logo(remote_logo, channel_identifier=key) if remote_logo else ""
                tvg_logo_local = old_logo if old_logo and old_logo != default_logo else default_logo
                if old_url != url or (old_logo != tvg_logo_local):
                    c.execute("""
                        UPDATE channels 
                        SET url = ?, logo_url = ?, name = ?, group_title = ?
                        WHERE id = ?
                    """, (url, tvg_logo_local, name_part, group_title, channel_id))
                    print(f"[INFO] Updated channel '{key}' with new URL and logo.")
            else:
                tvg_logo_local = cache_logo(remote_logo, channel_identifier=key) if remote_logo else ""
                c.execute("""
                    INSERT INTO channels (name, url, tvg_name, logo_url, group_title, active)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (name_part, url, tvg_name, tvg_logo_local, group_title, 0))
                new_id = c.lastrowid
                # Assign the channel_number to be equal to the new primary key by default.
                c.execute("UPDATE channels SET channel_number = ? WHERE id = ?", (new_id, new_id))
                print(f"[INFO] Inserted new channel '{key}' with logo. (Default inactive, channel_number set to {new_id})")
            idx += 2
        else:
            idx += 1

    conn.commit()
    conn.close()

    print("[INFO] Channels updated. Updating modified EPG file...")
    parse_raw_epg_files()
    build_combined_epg()