import os
import sqlite3
import subprocess
import threading
import socket
import queue
import xml.etree.ElementTree as ET

from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.responses import (
    StreamingResponse, JSONResponse, Response, HTMLResponse,
    PlainTextResponse, FileResponse, RedirectResponse
)
from fastapi.templating import Jinja2Templates

from fastapi.staticfiles import StaticFiles

# -- Configuration and Directory Setup --
CONFIG_DIR = "config"
M3U_DIR = os.path.join(CONFIG_DIR, "m3u")
EPG_DIR = os.path.join(CONFIG_DIR, "epg")
DB_FILE = os.path.join(CONFIG_DIR, "iptv_channels.db")
MODIFIED_EPG_DIR = os.path.join(CONFIG_DIR, "epg_modified")

os.makedirs(CONFIG_DIR, exist_ok=True)
os.makedirs(M3U_DIR, exist_ok=True)
os.makedirs(EPG_DIR, exist_ok=True)
os.makedirs(MODIFIED_EPG_DIR, exist_ok=True)

shared_streams = {}
streams_lock = threading.Lock()

#############################
# Database Initialization   #
#############################

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # Create the channels table with the new group_title column
    c.execute('''
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY,
            name TEXT,
            url TEXT,
            tvg_name TEXT,
            logo_url TEXT,
            group_title TEXT
        )
    ''')

    # In case the table already existed, try to add columns if they do not exist.
    try:
        c.execute('ALTER TABLE channels ADD COLUMN logo_url TEXT')
    except sqlite3.OperationalError:
        pass
    try:
        c.execute('ALTER TABLE channels ADD COLUMN group_title TEXT')
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

#############################
# EPG Parsing Function      #
#############################

def parse_epg_files():
    epg_files = [
        os.path.join(EPG_DIR, f)
        for f in os.listdir(EPG_DIR)
        if f.endswith(".xml") or f.endswith(".xmltv")
    ]
    if not epg_files:
        print("[INFO] No EPG files found.")
        return
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM epg_programs")
    c.execute("SELECT tvg_name, id FROM channels")
    rows = c.fetchall()
    tvgname_to_dbid = {row[0]: row[1] for row in rows if row[0]}
    c.execute("SELECT tvg_name, logo_url FROM channels")
    logo_map = dict(c.fetchall())

    for epg_file in epg_files:
        print(f"[INFO] Parsing EPG: {epg_file}")
        try:
            tree = ET.parse(epg_file)
            root = tree.getroot()
            oldid_to_newid = {}
            for channel_el in list(root.findall("channel")):
                old_epg_id = channel_el.get("id", "").strip()
                display_name_el = channel_el.find("display-name")
                display_name = display_name_el.text.strip() if (display_name_el is not None and display_name_el.text) else ""
                new_id = None
                if old_epg_id in tvgname_to_dbid:
                    new_id = tvgname_to_dbid[old_epg_id]
                elif display_name in tvgname_to_dbid:
                    new_id = tvgname_to_dbid[display_name]
                if not new_id:
                    root.remove(channel_el)
                    continue
                channel_el.set("id", str(new_id))
                oldid_to_newid[old_epg_id] = str(new_id)
                if display_name in logo_map and logo_map[display_name]:
                    icon_el = channel_el.find("icon")
                    if icon_el is None:
                        icon_el = ET.SubElement(channel_el, "icon")
                    icon_el.set("src", logo_map[display_name])
            for prog_el in list(root.findall("programme")):
                prog_channel = prog_el.get("channel", "").strip()
                if prog_channel not in oldid_to_newid:
                    root.remove(prog_el)
                    continue
                new_prog_channel_id = oldid_to_newid[prog_channel]
                prog_el.set("channel", new_prog_channel_id)
                start_time = prog_el.get("start", "").strip()
                stop_time = prog_el.get("stop", "").strip()
                title_el = prog_el.find("title")
                desc_el = prog_el.find("desc")
                title_text = title_el.text.strip() if (title_el is not None and title_el.text) else ""
                desc_text = desc_el.text.strip() if (desc_el is not None and desc_el.text) else ""
                c.execute("""
                    INSERT INTO epg_programs
                    (channel_tvg_name, start, stop, title, description)
                    VALUES (?, ?, ?, ?, ?)
                """, (new_prog_channel_id, start_time, stop_time, title_text, desc_text))
            modified_epg_file = os.path.join(MODIFIED_EPG_DIR, os.path.basename(epg_file))
            tree.write(modified_epg_file, encoding="utf-8", xml_declaration=True)
            print(f"[SUCCESS] Filtered EPG saved as {modified_epg_file}")
        except Exception as e:
            print(f"[ERROR] Parsing {epg_file} failed: {e}")
    conn.commit()
    conn.close()

#############################
# M3U Parsing Function      #
#############################

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
    return line[start:end]

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
                name_part = line.split(",", 1)[-1].strip()
                tvg_name = parse_m3u_attribute(line, "tvg-name")
                tvg_logo = parse_m3u_attribute(line, "tvg-logo")
                group_title = parse_m3u_attribute(line, "group-title")
                if (idx + 1) < len(lines):
                    url = lines[idx + 1].strip()
                else:
                    url = ""

                # Use tvg_name if available; otherwise fall back to the channel name.
                key = tvg_name if tvg_name else name_part

                c.execute("SELECT id, url FROM channels WHERE tvg_name = ? OR name = ?", (key, key))
                row = c.fetchone()
                if row:
                    channel_id, old_url = row
                    if old_url != url:
                        c.execute("""
                            UPDATE channels 
                            SET url = ?, logo_url = ?, name = ?, group_title = ?
                            WHERE id = ?
                        """, (url, tvg_logo, name_part, group_title, channel_id))
                        print(f"[INFO] Updated channel '{key}' with a new URL.")
                else:
                    c.execute("""
                        INSERT INTO channels (name, url, tvg_name, logo_url, group_title)
                        VALUES (?, ?, ?, ?, ?)
                    """, (name_part, url, tvg_name, tvg_logo, group_title))
                    print(f"[INFO] Inserted new channel '{key}'.")
                idx += 2
            else:
                idx += 1

    conn.commit()
    conn.close()

    print("[INFO] Channels updated. Updating modified EPG file...")
    parse_epg_files()

#############################
# FastAPI App Initialization
#############################

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Mount the static directory so that files in /static/ are served
app.mount("/static", StaticFiles(directory="static"), name="static")

# Registering a startup event to defer execution until all functions are defined.
@app.on_event("startup")
def startup_event():
    init_db()          # Ensure the database and tables are created
    load_m3u_files()   # Now it's safe to load the M3U files

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
    c.execute("SELECT id, name, url, logo_url FROM channels")
    rows = c.fetchall()
    conn.close()
    lineup_data = []
    for ch_id, ch_name, ch_url, logo_url in rows:
        guide_number = str(ch_id)
        channel_obj = {
            "GuideNumber": guide_number,
            "GuideName": ch_name,
            "Station": guide_number,
            "Logo": logo_url if logo_url else "",
            "URL": f"{base_url}/tuner/{ch_id}"
        }
        lineup_data.append(channel_obj)
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
    modified_epg_files = [
        os.path.join(MODIFIED_EPG_DIR, f)
        for f in os.listdir(MODIFIED_EPG_DIR)
        if f.endswith(".xml") or f.endswith(".xmltv")
    ]
    if modified_epg_files:
        return FileResponse(modified_epg_files[0], media_type="application/xml")
    return PlainTextResponse("<tv></tv>", media_type="application/xml")

class SharedStream:
    def __init__(self, ffmpeg_cmd):
        self.ffmpeg_cmd = ffmpeg_cmd
        self.process = subprocess.Popen(
            ffmpeg_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=10**8
        )
        self.subscribers = []
        self.lock = threading.Lock()
        self.is_running = True
        self.broadcast_thread = threading.Thread(target=self._broadcast)
        self.broadcast_thread.daemon = True
        self.broadcast_thread.start()

    def _broadcast(self):
        while True:
            chunk = self.process.stdout.read(1024)
            if not chunk:
                print("No chunk received from FFmpeg; ending broadcast.")
                break
            with self.lock:
                for q in self.subscribers:
                    q.put(chunk)
        self.is_running = False
        with self.lock:
            for q in self.subscribers:
                q.put(None)
        stderr_output = self.process.stderr.read()
        if stderr_output:
            print("FFmpeg stderr:", stderr_output.decode('utf-8', errors='ignore'))

    def add_subscriber(self):
        q = queue.Queue()
        with self.lock:
            self.subscribers.append(q)
        return q

    def remove_subscriber(self, q):
        with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)
            if not self.subscribers:
                try:
                    self.process.kill()
                except Exception:
                    pass

def get_shared_stream(channel_id: int, stream_url: str) -> SharedStream:
    print(f"Creating shared stream for channel {channel_id} with URL: {stream_url}")
    ffmpeg_cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-user_agent", "VLC/3.0.20-git LibVLC/3.0.20-git",
        "-re", "-i", stream_url,
        "-max_muxing_queue_size", "1024",
        "-c:v", "copy", "-c:a", "aac",
        "-preset", "ultrafast",
        "-f", "mpegts", "pipe:1"
    ]
    with streams_lock:
        if channel_id in shared_streams and shared_streams[channel_id].is_running:
            return shared_streams[channel_id]
        shared_streams[channel_id] = SharedStream(ffmpeg_cmd)
        return shared_streams[channel_id]

@app.get("/tuner/{channel_id}")
def tuner_stream(channel_id: int, request: Request):
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
    shared = get_shared_stream(channel_id, stream_url)
    subscriber_queue = shared.add_subscriber()

    def streamer():
        try:
            while True:
                chunk = subscriber_queue.get()
                if chunk is None:
                    break
                yield chunk
        finally:
            shared.remove_subscriber(subscriber_queue)
            with streams_lock:
                if not shared.subscribers:
                    shared_streams.pop(channel_id, None)

    return StreamingResponse(streamer(), media_type="video/mp2t")

@app.get("/web", response_class=HTMLResponse)
def web_interface(request: Request):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, name, url, tvg_name, logo_url, group_title FROM channels ORDER BY id")
    channels = c.fetchall()
    epg_map = {}
    for ch_id, ch_name, ch_url, ch_tvg_name, ch_logo, ch_group in channels:
        c.execute("""
            SELECT title, start, stop, description
            FROM epg_programs
            WHERE channel_tvg_name = ?
            ORDER BY start ASC
            LIMIT 1
        """, (str(ch_id),))
        program = c.fetchone()
        if program:
            epg_map[str(ch_id)] = {
                "title": program[0],
                "start": program[1],
                "stop": program[2],
                "description": program[3]
            }
        else:
            epg_map[str(ch_id)] = None
    conn.close()
    return templates.TemplateResponse("index.html", {
        "request": request,
        "channels": channels,
        "epg_map": epg_map
    })

###############################################################################
# NEW: Functions to update (or swap) channel numbers and update streams accordingly
###############################################################################

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

def update_modified_epg(old_id: int, new_id: int, swap: bool):
    epg_files = [os.path.join(MODIFIED_EPG_DIR, f) for f in os.listdir(MODIFIED_EPG_DIR)
                 if f.endswith(".xml") or f.endswith(".xmltv")]
    if not epg_files:
        return
    epg_file = epg_files[0]
    try:
        tree = ET.parse(epg_file)
        root = tree.getroot()
        if swap:
            for ch in root.findall("channel"):
                id_val = ch.get("id")
                if id_val == str(old_id):
                    ch.set("id", str(new_id))
                elif id_val == str(new_id):
                    ch.set("id", str(old_id))
            for prog in root.findall("programme"):
                chan = prog.get("channel")
                if chan == str(old_id):
                    prog.set("channel", str(new_id))
                elif chan == str(new_id):
                    prog.set("channel", str(old_id))
        else:
            for ch in root.findall("channel"):
                if ch.get("id") == str(old_id):
                    ch.set("id", str(new_id))
            for prog in root.findall("programme"):
                if prog.get("channel") == str(old_id):
                    prog.set("channel", str(new_id))
        tree.write(epg_file, encoding="utf-8", xml_declaration=True)
    except Exception as e:
        print("Error updating modified EPG file:", e)

def clear_shared_stream(channel_id: int):
    """
    Kill and remove the shared stream for a given channel id (if one exists).
    """
    with streams_lock:
        if channel_id in shared_streams:
            try:
                shared_streams[channel_id].process.kill()
            except Exception:
                pass
            del shared_streams[channel_id]

@app.post("/update_channel_number")
def update_channel_number(current_id: int = Form(...), new_id: int = Form(...)):
    if current_id == new_id:
        return RedirectResponse(url="/web", status_code=303)
    swap = swap_channel_ids(current_id, new_id)
    update_modified_epg(current_id, new_id, swap)
    # Clear any active shared streams for these channel IDs so new streams are created.
    clear_shared_stream(current_id)
    clear_shared_stream(new_id)
    return RedirectResponse(url="/web", status_code=303)
