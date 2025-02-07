import os
import sqlite3
import html
import hashlib
import requests
from .config import M3U_DIR, DB_FILE, LOGOS_DIR
from .epg import parse_epg_files

def cache_logo(logo_url: str) -> str:
    """
    Download the image at logo_url (if not already cached) and return a local URL
    (e.g. /static/logos/<filename>). If the download fails, return the original URL.
    """
    if not logo_url:
        return logo_url  # nothing to cache

    try:
        # Create a unique filename based on the URL.
        h = hashlib.md5(logo_url.encode("utf-8")).hexdigest()
        # Try to use the extension from the URL, default to .jpg if not found.
        ext = os.path.splitext(logo_url)[1]
        if not ext or len(ext) > 5:
            ext = ".jpg"
        filename = f"{h}{ext}"
        filepath = os.path.join(LOGOS_DIR, filename)
        # If the file does not exist, download and save it.
        if not os.path.exists(filepath):
            response = requests.get(logo_url, timeout=10)
            if response.status_code == 200:
                with open(filepath, "wb") as f:
                    f.write(response.content)
            else:
                print(f"Warning: Failed to download logo {logo_url} (status: {response.status_code}).")
                return logo_url
        # Return the local URL to be used by the app.
        # Adjust the URL path as needed; here we assume your static files are served from /static/.
        return f"/static/logos/{filename}"
    except Exception as e:
        print(f"Error caching logo {logo_url}: {e}")
        return logo_url  # fallback to original URL if any error occurs

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

                # For caching purposes, record the remote logo URL.
                remote_logo = tvg_logo if tvg_logo else ""
                # Use tvg_name if available; otherwise fall back to the channel name.
                key = tvg_name if tvg_name else name_part

                # Check if this channel is already in the database.
                c.execute("SELECT id, url, logo_url FROM channels WHERE tvg_name = ? OR name = ?", (key, key))
                row = c.fetchone()
                if row:
                    channel_id, old_url, old_logo = row
                    # If we already have a logo cached (i.e. old_logo starts with /static/logos/),
                    # then keep that value. Otherwise, cache the current remote logo.
                    if old_logo and old_logo.startswith("/static/logos/"):
                        tvg_logo_local = old_logo
                    else:
                        tvg_logo_local = cache_logo(remote_logo)
                    # Update the record if the stream URL or logo URL has changed.
                    if old_url != url or (old_logo != tvg_logo_local):
                        c.execute("""
                            UPDATE channels 
                            SET url = ?, logo_url = ?, name = ?, group_title = ?
                            WHERE id = ?
                        """, (url, tvg_logo_local, name_part, group_title, channel_id))
                        print(f"[INFO] Updated channel '{key}' with new URL and logo.")
                else:
                    # New channel: cache the logo (if provided) and insert the record.
                    tvg_logo_local = cache_logo(remote_logo) if remote_logo else ""
                    c.execute("""
                        INSERT INTO channels (name, url, tvg_name, logo_url, group_title)
                        VALUES (?, ?, ?, ?, ?)
                    """, (name_part, url, tvg_name, tvg_logo_local, group_title))
                    print(f"[INFO] Inserted new channel '{key}' with logo.")
                idx += 2
            else:
                idx += 1

    conn.commit()
    conn.close()

    print("[INFO] Channels updated. Updating modified EPG file...")
    parse_epg_files()