import os
import sqlite3
import subprocess
import threading
import socket
import xml.etree.ElementTree as ET
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import (
    StreamingResponse, JSONResponse, Response, HTMLResponse, PlainTextResponse, FileResponse
)
from fastapi.templating import Jinja2Templates

#############################
# 1) Config & Directories   #
#############################

CONFIG_DIR = "config"
M3U_DIR = os.path.join(CONFIG_DIR, "m3u")
EPG_DIR = os.path.join(CONFIG_DIR, "epg")
DB_FILE = os.path.join(CONFIG_DIR, "iptv_channels.db")

os.makedirs(CONFIG_DIR, exist_ok=True)
os.makedirs(M3U_DIR, exist_ok=True)
os.makedirs(EPG_DIR, exist_ok=True)

#############################
# 2) Database Initialization
#############################

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # Create channels table (with logo_url column)
    c.execute('''
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY,
            name TEXT,
            url TEXT,
            tvg_name TEXT,
            logo_url TEXT
        )
    ''')

    # Attempt to add logo_url if an older DB existed without it.
    # This will silently fail if the column already exists.
    try:
        c.execute('ALTER TABLE channels ADD COLUMN logo_url TEXT')
    except sqlite3.OperationalError:
        pass

    # Create EPG table
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

init_db()

#############################
# 3) Parse M3U Files        #
#############################

def find_m3u_files():
    return [
        os.path.join(M3U_DIR, f)
        for f in os.listdir(M3U_DIR)
        if f.endswith(".m3u")
    ]

def parse_m3u_attribute(line: str, attr_name: str) -> str:
    """
    Look for attr_name="..." in the #EXTINF line. Return "" if not found.
    Example usage: parse_m3u_attribute(line, "tvg-name") or parse_m3u_attribute(line, "tvg-logo")
    """
    lower_line = line.lower()
    key = f'{attr_name.lower()}="'
    start = lower_line.find(key)
    if start == -1:
        return ""
    start += len(key)
    end = lower_line.find('"', start)
    if end == -1:
        return ""
    return line[start:end]

def load_m3u_files():
    m3u_files = find_m3u_files()
    if not m3u_files:
        print("[INFO] No M3U files found.")
        return

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM channels")  # Wipe old channels

    for m3u_file in m3u_files:
        print(f"[INFO] Loading M3U: {m3u_file}")
        with open(m3u_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        idx = 0
        while idx < len(lines):
            line = lines[idx].strip()
            if line.startswith("#EXTINF"):
                # Extract channel's display name from after the last comma
                name_part = line.split(",", 1)[-1].strip()

                # Parse out tvg-name and tvg-logo
                tvg_name = parse_m3u_attribute(line, "tvg-name")
                tvg_logo = parse_m3u_attribute(line, "tvg-logo")

                # Next line is the stream URL
                if (idx + 1) < len(lines):
                    url = lines[idx + 1].strip()
                else:
                    url = ""

                c.execute("""
                    INSERT INTO channels (name, url, tvg_name, logo_url)
                    VALUES (?, ?, ?, ?)
                """, (name_part, url, tvg_name, tvg_logo))
                idx += 2
            else:
                idx += 1

    conn.commit()
    conn.close()
    print("[SUCCESS] M3U loaded!")

load_m3u_files()

#############################
# 4) Parse EPG Files        #
#############################

def find_epg_files():
    return [
        os.path.join(EPG_DIR, f)
        for f in os.listdir(EPG_DIR)
        if f.endswith(".xml") or f.endswith(".xmltv")
    ]

def parse_epg_files():
    epg_files = find_epg_files()
    if not epg_files:
        print("[INFO] No EPG files found.")
        return

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM epg_programs")  # wipe old EPG data

    for epg_file in epg_files:
        print(f"[INFO] Parsing EPG: {epg_file}")
        try:
            tree = ET.parse(epg_file)
            root = tree.getroot()

            # Map channel_id -> display_name, plus detect logo from <icon src="..."/>
            channel_id_to_display_name = {}
            channel_id_to_logo = {}

            for channel_el in root.findall("channel"):
                cid = channel_el.get("id", "")
                disp = channel_el.find("display-name")
                icon_el = channel_el.find("icon")

                if disp is not None and disp.text:
                    disp_text = disp.text.strip()
                    channel_id_to_display_name[cid] = disp_text

                    # If there's an <icon src="..."/>, capture it
                    if icon_el is not None and icon_el.get("src"):
                        logo_url = icon_el.get("src").strip()
                        channel_id_to_logo[cid] = logo_url

            # Update channels table with EPG logos if they match tvg_name == display_name
            for cid, disp_name in channel_id_to_display_name.items():
                logo_url = channel_id_to_logo.get(cid, "")
                if logo_url:
                    c.execute("""
                        UPDATE channels
                        SET logo_url = ?
                        WHERE tvg_name = ?
                    """, (logo_url, disp_name))

            # Insert <programme> elements into epg_programs
            for prog_el in root.findall("programme"):
                prog_cid = prog_el.get("channel", "")
                start_time = prog_el.get("start", "").strip()
                stop_time = prog_el.get("stop", "").strip()

                title_el = prog_el.find("title")
                desc_el = prog_el.find("desc")

                title_text = title_el.text.strip() if title_el is not None and title_el.text else ""
                desc_text = desc_el.text.strip() if desc_el is not None and desc_el.text else ""

                # The display name for this channel
                display_name = channel_id_to_display_name.get(prog_cid, "")
                if display_name:
                    c.execute("""
                        INSERT INTO epg_programs
                        (channel_tvg_name, start, stop, title, description)
                        VALUES (?, ?, ?, ?, ?)
                    """, (display_name, start_time, stop_time, title_text, desc_text))
            
            conn.commit()
            print(f"[SUCCESS] EPG loaded from {epg_file}")
        except Exception as e:
            print(f"[ERROR] Parsing {epg_file} failed: {e}")

    conn.close()

parse_epg_files()

#############################
# 5) Set up FastAPI         #
#############################

app = FastAPI()
templates = Jinja2Templates(directory="templates")

@app.get("/discover.json")
def discover(request: Request):
    base_url = f"{request.url.scheme}://{request.client.host}:{request.url.port}"
    return JSONResponse({
        "FriendlyName": "IPTV HDHomeRun",
        "Manufacturer": "Custom",
        "ModelNumber": "HDTC-2US",
        "FirmwareName": "hdhomeruntc_atsc",
        "FirmwareVersion": "20250802",
        "DeviceID": "12345678",
        "DeviceAuth": "testauth",
        "BaseURL": base_url,
        "LineupURL": f"{base_url}/lineup.json"
    })

@app.get("/lineup.json")
def lineup(request: Request):
    base_url = f"{request.url.scheme}://{request.client.host}:{request.url.port}"
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, name, url FROM channels")
    rows = c.fetchall()
    conn.close()

    lineup_data = [
        {
            "GuideNumber": str(ch_id),
            "GuideName": ch_name,
            "URL": f"{base_url}/tuner/{ch_id}"
        }
        for ch_id, ch_name, ch_url in rows
    ]
    return JSONResponse(lineup_data)

@app.get("/lineup_status.json")
def lineup_status():
    return JSONResponse({
        "ScanInProgress": 0,
        "ScanPossible": 1,
        "Source": "Cable",
        "SourceList": ["Cable"]
    })

@app.get("/epg.xml")
def serve_epg():
    epg_files = find_epg_files()
    if not epg_files:
        return PlainTextResponse("<tv></tv>", media_type="application/xml")
    return FileResponse(epg_files[0], media_type="application/xml")

@app.get("/tuner/{channel_id}")
def tuner_stream(channel_id: int):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT url FROM channels WHERE id=?", (channel_id,))
    row = c.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Channel not found.")

    stream_url = row[0]
    if not stream_url:
        raise HTTPException(status_code=404, detail="Invalid channel URL.")

    # FFmpeg command for remuxing/re-encoding
    ffmpeg_cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-user_agent", "VLC/3.0.20-git LibVLC/3.0.20-git",
        "-re", "-i", stream_url,
        "-max_muxing_queue_size", "1024",
        "-c:v", "copy", "-c:a", "aac",
        "-preset", "ultrafast",
        "-f", "mpegts", "pipe:1"
    ]

    try:
        process = subprocess.Popen(
            ffmpeg_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=10**8
        )

        # Spawn a thread to read and log stderr
        thread = threading.Thread(target=read_ffmpeg_stderr, args=(process,))
        thread.daemon = True
        thread.start()

        return StreamingResponse(process.stdout, media_type="video/mp2t")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start FFmpeg: {str(e)}")

def read_ffmpeg_stderr(process):
    """
    Background thread: log FFmpeg stderr for debugging.
    """
    for line in iter(process.stderr.readline, b""):
        print("[FFmpeg stderr]", line.decode("utf-8", errors="ignore"))

@app.get("/web", response_class=HTMLResponse)
def web_interface(request: Request):
    """
    Render a simple web UI to display channels and upcoming EPG entries.
    """
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # Fetch all channels (including logo_url)
    c.execute("SELECT id, name, url, tvg_name, logo_url FROM channels ORDER BY id")
    channels = c.fetchall()

    # For each channel, fetch the first upcoming program
    epg_map = {}
    for ch_id, ch_name, ch_url, ch_tvg_name, ch_logo in channels:
        c.execute("""
            SELECT title, start, stop, description
            FROM epg_programs
            WHERE channel_tvg_name = ?
            ORDER BY start ASC
            LIMIT 1
        """, (ch_tvg_name,))
        program = c.fetchone()

        if program:
            epg_map[ch_tvg_name] = {
                "title": program[0],
                "start": program[1],
                "stop": program[2],
                "description": program[3]
            }
        else:
            epg_map[ch_tvg_name] = None

    conn.close()

    # Render the HTML template (index.html)
    return templates.TemplateResponse("index.html", {
        "request": request,
        "channels": channels,
        "epg_map": epg_map
    })
