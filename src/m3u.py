import os
import sqlite3
import html
import hashlib
import requests
from .config import M3U_DIR, DB_FILE, LOGOS_DIR
from .epg import parse_epg_files

import os
import hashlib
import requests
from .config import LOGOS_DIR

import os
import re
import hashlib
import requests
from .config import LOGOS_DIR

def cache_logo(logo_url: str, channel_identifier: str = None) -> str:
    """
    Download the image at logo_url (if not already cached or if the cached file is invalid)
    and return a local URL (e.g. /static/logos/<filename>).

    The filename is generated in a searchable way using the channel_identifier (typically the
    tvg-name from the M3U file) if provided. Non-alphanumeric characters are replaced with underscores
    for compatibility with file systems. An 8-character hash from the logo URL is appended for uniqueness.
    If no valid channel_identifier is provided, the function falls back to using the full MD5 hash of the logo_url.
    """
    if not logo_url:
        return logo_url  # nothing to cache

    try:
        # Ensure the logos directory exists.
        logos_dir = os.path.abspath(LOGOS_DIR)
        os.makedirs(logos_dir, exist_ok=True)

        # Try to use the extension from the URL; default to .jpg if not found.
        ext = os.path.splitext(logo_url)[1]
        if not ext or len(ext) > 5:
            ext = ".jpg"

        # If channel_identifier is provided and not empty, sanitize it.
        if channel_identifier and channel_identifier.strip():
            # Replace any character that is not alphanumeric, underscore, or hyphen with an underscore.
            sanitized = re.sub(r'[^\w\-]+', '_', channel_identifier.strip().lower())
            if sanitized:
                # Append an 8-character hash for uniqueness.
                h = hashlib.md5(logo_url.encode("utf-8")).hexdigest()[:8]
                filename = f"{sanitized}_{h}{ext}"
            else:
                # If sanitization resulted in an empty string, fallback.
                h = hashlib.md5(logo_url.encode("utf-8")).hexdigest()
                filename = f"{h}{ext}"
        else:
            # No channel identifier provided; use the full hash.
            h = hashlib.md5(logo_url.encode("utf-8")).hexdigest()
            filename = f"{h}{ext}"

        filepath = os.path.join(logos_dir, filename)

        # If the file exists and is valid, return the URL.
        if os.path.exists(filepath):
            if os.path.getsize(filepath) > 0:
                return f"/static/logos/{filename}"
            else:
                os.remove(filepath)

        # File doesn't exist (or is invalid); download and save it.
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

def find_m3u_files():
    os.makedirs(M3U_DIR, exist_ok=True)
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
                # Extract the display channel name (after the comma)
                name_part = line.split(",", 1)[-1].strip()
                # Extract attributes.
                tvg_name = parse_m3u_attribute(line, "tvg-name")
                tvg_logo = parse_m3u_attribute(line, "tvg-logo")
                group_title = parse_m3u_attribute(line, "group-title")
                # Next line is the stream URL.
                if (idx + 1) < len(lines):
                    url = lines[idx + 1].strip()
                else:
                    url = ""

                # Use tvg_logo if provided.
                remote_logo = tvg_logo if tvg_logo else ""
                # Use tvg_name as identifier if available, otherwise the display name.
                key = tvg_name if tvg_name else name_part

                # Check if this channel already exists.
                c.execute("SELECT id, url, logo_url FROM channels WHERE tvg_name = ? OR name = ?", (key, key))
                row = c.fetchone()
                if row:
                    channel_id, old_url, old_logo = row
                    tvg_logo_local = ""
                    # If a cached logo already exists, verify its validity.
                    if old_logo and old_logo.startswith("/static/logos/"):
                        filename = old_logo.split("/static/logos/")[-1]
                        logos_dir = os.path.abspath(LOGOS_DIR)
                        filepath = os.path.join(logos_dir, filename)
                        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                            tvg_logo_local = old_logo
                        else:
                            # Recache if the file is missing or invalid.
                            tvg_logo_local = cache_logo(remote_logo, channel_identifier=key)
                    else:
                        tvg_logo_local = cache_logo(remote_logo, channel_identifier=key)
                    
                    # Update the record if the URL or logo has changed.
                    if old_url != url or (old_logo != tvg_logo_local):
                        c.execute("""
                            UPDATE channels 
                            SET url = ?, logo_url = ?, name = ?, group_title = ?
                            WHERE id = ?
                        """, (url, tvg_logo_local, name_part, group_title, channel_id))
                        print(f"[INFO] Updated channel '{key}' with new URL and logo.")
                else:
                    # New channel: cache the logo (if provided) and insert a new record.
                    tvg_logo_local = cache_logo(remote_logo, channel_identifier=key) if remote_logo else ""
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