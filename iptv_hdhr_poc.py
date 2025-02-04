import os
import sqlite3
import subprocess
import threading
import socket
import queue
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
MODIFIED_EPG_DIR = os.path.join(CONFIG_DIR, "epg_modified")

os.makedirs(CONFIG_DIR, exist_ok=True)
os.makedirs(M3U_DIR, exist_ok=True)
os.makedirs(EPG_DIR, exist_ok=True)
os.makedirs(MODIFIED_EPG_DIR, exist_ok=True)

shared_streams = {}
streams_lock = threading.Lock()

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

    # Get the list of tvg_names from M3U channels
    c.execute("SELECT DISTINCT tvg_name FROM channels WHERE tvg_name IS NOT NULL AND tvg_name != ''")
    m3u_tvg_names = set(row[0] for row in c.fetchall())

    c.execute("DELETE FROM epg_programs")  # Wipe old EPG data

    for epg_file in epg_files:
        print(f"[INFO] Parsing EPG: {epg_file}")
        try:
            tree = ET.parse(epg_file)
            root = tree.getroot()

            # Map channel_id -> tvg_name -> logo_url
            channel_id_to_display_name = {}
            channel_id_to_logo = {}

            # Fetch logos from the database
            c.execute("SELECT tvg_name, logo_url FROM channels")
            for tvg_name, logo_url in c.fetchall():
                if tvg_name and logo_url:
                    channel_id_to_logo[tvg_name] = logo_url

            # Create a set of valid channel IDs based on M3U
            valid_channel_ids = set()

            # **Filter <channel> elements based on M3U tvg_names**
            for channel_el in list(root.findall("channel")):
                channel_id = channel_el.get("id", "")
                display_name_el = channel_el.find("display-name")

                if display_name_el is not None and display_name_el.text:
                    display_name = display_name_el.text.strip()
                else:
                    display_name = ""

                if channel_id in m3u_tvg_names or display_name in m3u_tvg_names:
                    valid_channel_ids.add(channel_id)
                    channel_id_to_display_name[channel_id] = display_name

                    # Add logo if available
                    logo_url = channel_id_to_logo.get(display_name, "")
                    if logo_url:
                        icon_el = channel_el.find("icon")
                        if icon_el is None:
                            icon_el = ET.SubElement(channel_el, "icon")
                        icon_el.set("src", logo_url)
                else:
                    root.remove(channel_el)  # Remove unrelated channels

            # **Filter <programme> elements based on valid_channel_ids**
            for prog_el in list(root.findall("programme")):
                prog_channel_id = prog_el.get("channel", "")
                if prog_channel_id not in valid_channel_ids:
                    root.remove(prog_el)
                    continue

                start_time = prog_el.get("start", "").strip()
                stop_time = prog_el.get("stop", "").strip()
                title_el = prog_el.find("title")
                desc_el = prog_el.find("desc")

                title_text = title_el.text.strip() if title_el is not None and title_el.text else ""
                desc_text = desc_el.text.strip() if desc_el is not None and desc_el.text else ""

                display_name = channel_id_to_display_name.get(prog_channel_id, "")
                if display_name:
                    c.execute("""
                        INSERT INTO epg_programs
                        (channel_tvg_name, start, stop, title, description)
                        VALUES (?, ?, ?, ?, ?)
                    """, (display_name, start_time, stop_time, title_text, desc_text))

            # Save the modified EPG in a separate directory
            modified_epg_file = os.path.join(MODIFIED_EPG_DIR, os.path.basename(epg_file))
            tree.write(modified_epg_file, encoding="utf-8", xml_declaration=True)
            print(f"[SUCCESS] Filtered EPG saved as {modified_epg_file}")

        except Exception as e:
            print(f"[ERROR] Parsing {epg_file} failed: {e}")

    conn.commit()
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
    """
    Serve the modified EPG file instead of the original.
    If no modified EPG exists, return an empty <tv></tv>.
    """
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
        self.subscribers = []  # List of subscriber queues
        self.lock = threading.Lock()
        self.is_running = True
        # Start the thread that reads FFmpeg output and broadcasts to subscribers.
        self.broadcast_thread = threading.Thread(target=self._broadcast)
        self.broadcast_thread.daemon = True
        self.broadcast_thread.start()

    def _broadcast(self):
        """Continuously read from FFmpeg's stdout and send chunks to all subscribers."""
        while True:
            chunk = self.process.stdout.read(1024)
            if not chunk:
                print("No chunk received from FFmpeg; ending broadcast.")
                break  # End of stream
            #print(f"Broadcasting a chunk of size: {len(chunk)} bytes")
            with self.lock:
                for q in self.subscribers:
                    q.put(chunk)
        self.is_running = False
        # Signal end-of-stream to all subscribers.
        with self.lock:
            for q in self.subscribers:
                q.put(None)
        # Optionally log any FFmpeg errors.
        stderr_output = self.process.stderr.read()
        if stderr_output:
            print("FFmpeg stderr:", stderr_output.decode('utf-8', errors='ignore'))

    def add_subscriber(self):
        """Add a new subscriber (a queue) for a client."""
        q = queue.Queue()
        with self.lock:
            self.subscribers.append(q)
        return q

    def remove_subscriber(self, q):
        """Remove a subscriber. If no subscribers remain, kill the FFmpeg process."""
        with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)
            if not self.subscribers:
                try:
                    self.process.kill()
                except Exception:
                    pass

def get_shared_stream(channel_id: int, stream_url: str) -> SharedStream:
    """
    Return the SharedStream for a channel, creating a new one if needed.
    This uses the actual stream_url from the database.
    """
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
        # Create a new shared stream for this channel.
        shared_streams[channel_id] = SharedStream(ffmpeg_cmd)
        return shared_streams[channel_id]

@app.get("/tuner/{channel_id}")
def tuner_stream(channel_id: int, request: Request):
    # Look up the channel's stream URL from the database.
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

    # Obtain the shared stream object for this channel.
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
            # Clean up subscriber when the client disconnects.
            shared.remove_subscriber(subscriber_queue)
            with streams_lock:
                if not shared.subscribers:
                    shared_streams.pop(channel_id, None)

    return StreamingResponse(streamer(), media_type="video/mp2t")

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