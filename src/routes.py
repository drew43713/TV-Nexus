import os
import gzip
import socket
import sqlite3
from fastapi import APIRouter, Request, HTTPException, Form
from fastapi.responses import (
    JSONResponse, FileResponse, PlainTextResponse, StreamingResponse,
    HTMLResponse, RedirectResponse
)
from fastapi.templating import Jinja2Templates
from .config import DB_FILE, MODIFIED_EPG_DIR, EPG_DIR
from .database import swap_channel_ids
from .epg import update_modified_epg
from .streaming import get_shared_stream, clear_shared_stream
from .m3u import load_m3u_files
import xml.etree.ElementTree as ET

router = APIRouter()
templates = Jinja2Templates(directory="templates")

@router.get("/discover.json")
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

def get_local_ip():
    # If running in Docker, you can supply the host IP via an environment variable.
    host_ip = os.getenv("HOST_IP")
    if host_ip:
        return host_ip

    # Otherwise, try the usual method.
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # This doesn't actually establish a connection.
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip

@router.get("/lineup.json")
def lineup(request: Request):
    # Get the server's IP address using our helper function.
    server_ip = get_local_ip()
    
    # Build the base URL using the server's IP address.
    base_url = f"{request.url.scheme}://{server_ip}:{request.url.port}"
    
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

@router.get("/lineup_status.json")
def lineup_status():
    return JSONResponse({
        "ScanInProgress": 0,
        "ScanPossible": 1,
        "Source": "Cable",
        "SourceList": ["Cable"]
    })

@router.get("/epg.xml")
def serve_epg():
    modified_epg_files = [
        os.path.join(MODIFIED_EPG_DIR, f)
        for f in os.listdir(MODIFIED_EPG_DIR)
        if f.endswith(".xml") or f.endswith(".xmltv")
    ]
    if modified_epg_files:
        return FileResponse(modified_epg_files[0], media_type="application/xml")
    return PlainTextResponse("<tv></tv>", media_type="application/xml")

@router.get("/tuner/{channel_id}")
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
            # Remove the shared stream if there are no more subscribers.
            from .streaming import streams_lock, shared_streams
            with streams_lock:
                if not shared.subscribers:
                    shared_streams.pop(channel_id, None)

    return StreamingResponse(streamer(), media_type="video/mp2t")

from datetime import datetime

@router.get("/web", response_class=HTMLResponse)
def web_interface(request: Request):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, name, url, tvg_name, logo_url, group_title FROM channels ORDER BY id")
    channels = c.fetchall()
    server_ip = get_local_ip()

    epg_map = {}
    epg_entry_map = {}  # Store which EPG entry is assigned to each channel
    stream_map = {}  # Store the stream URL for each channel

    # Get base URL dynamically
    base_url = f"{request.url.scheme}://{server_ip}:{request.url.port}"
    
    # Format the current UTC time as in the database.  
    # For example, if the DB value is like "20250210220000 +0000" (YYYYMMDDHHMMSS +0000):
    now = datetime.utcnow().strftime("%Y%m%d%H%M%S") + " +0000"

    for ch_id, ch_name, ch_url, ch_tvg_name, ch_logo, ch_group in channels:
        # Fetch the current EPG program details by selecting the one that is "active" now.
        c.execute("""
            SELECT title, start, stop, description
            FROM epg_programs
            WHERE channel_tvg_name = ? AND start <= ? AND stop > ?
            ORDER BY start DESC
            LIMIT 1
        """, (str(ch_id), now, now))
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

        # Store the EPG entry name
        epg_entry_map[str(ch_id)] = ch_tvg_name  

        # Generate the stream URL from FastAPI's `/tuner/{channel_id}`
        stream_map[str(ch_id)] = f"{base_url}/tuner/{ch_id}"

    conn.close()

    js_script = """
    <script>
        function handleRowClick(row) {
            const channelId = row.getAttribute('data-channel');
            const channelName = row.getAttribute('data-name');
            const logo = row.getAttribute('data-logo');
            const epgTitle = row.getAttribute('data-epg');
            const epgEntry = row.getAttribute('data-epg-entry');  
            const streamUrl = row.getAttribute('data-stream-url');  

            document.getElementById('modal-title').innerText = channelName;
            document.getElementById('modal-logo').src = logo;
            document.getElementById('modal-channel').innerText = channelId;
            document.getElementById('modal-epg').innerText = epgTitle;
            document.getElementById('modal-epg-entry').innerText = epgEntry;
            document.getElementById('modal-stream-url').innerText = streamUrl;
            document.getElementById('modal-stream-url').href = streamUrl;

            document.getElementById('modal').style.display = 'block';
            document.getElementById('overlay').style.display = 'block';
        }
        function closeModal() {
            document.getElementById('modal').style.display = 'none';
            document.getElementById('overlay').style.display = 'none';
        }
    </script>
    """

    return templates.TemplateResponse("index.html", {
        "request": request,
        "channels": channels,
        "epg_map": epg_map,
        "epg_entry_map": epg_entry_map,  
        "stream_map": stream_map,  # Pass stream URL mapping
        "js_script": js_script
    })

@router.post("/update_channel_number")
def update_channel_number(current_id: int = Form(...), new_id: int = Form(...)):
    if current_id == new_id:
        return RedirectResponse(url="/web", status_code=303)
    swap = swap_channel_ids(current_id, new_id)
    update_modified_epg(current_id, new_id, swap)
    # Clear any active shared streams for these channel IDs so new streams are created.
    clear_shared_stream(current_id)
    clear_shared_stream(new_id)
    return RedirectResponse(url="/web", status_code=303)

@router.get("/api/epg_entries")
def get_epg_entries():
    """
    Return a JSON list of available EPG entries in alphabetical order.
    """
    epg_entries = set()
    try:
        for f in os.listdir(EPG_DIR):
            if f.lower().endswith((".xml", ".xmltv", ".gz")):
                file_path = os.path.join(EPG_DIR, f)
                with open(file_path, "rb") as f_obj:
                    magic = f_obj.read(2)
                    f_obj.seek(0)
                    if magic == b'\x1f\x8b':
                        with gzip.open(f_obj, "rb") as gz_obj:
                            decompressed_data = gz_obj.read()
                        root = ET.fromstring(decompressed_data)
                        tree = ET.ElementTree(root)
                    else:
                        tree = ET.parse(f_obj)
                        root = tree.getroot()
                for channel_el in root.findall("channel"):
                    display_el = channel_el.find("display-name")
                    if display_el is not None and display_el.text:
                        epg_entries.add(display_el.text.strip())
        # Return the sorted list
        return JSONResponse(sorted(list(epg_entries)))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@router.post("/update_epg_entry")
def update_epg_entry(channel_id: int = Form(...), new_epg_entry: str = Form(...)):
    """
    Update the channel's tvg_name in the database and rebuild only the programme data
    in the modified EPG file for that channel.
    """
    try:
        # Update the channel's tvg_name in the database.
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT tvg_name FROM channels WHERE id = ?", (channel_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            return JSONResponse({"success": False, "error": "Channel not found."})
        current_tvg_name = row[0]
        c.execute("UPDATE channels SET tvg_name = ? WHERE id = ?", (new_epg_entry, channel_id))
        conn.commit()
        conn.close()

        # Update only the programme data in the modified EPG file for this channel.
        from .epg import update_program_data_for_channel
        update_program_data_for_channel(channel_id)

        return JSONResponse({"success": True})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@router.get("/api/current_program")
def get_current_program(channel_id: int):
    """
    Returns the current programme for a given channel based on the modified EPG data.
    Uses the current UTC time to query the epg_programs table.
    """
    now = datetime.utcnow().strftime("%Y%m%d%H%M%S") + " +0000"
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        SELECT title, start, stop, description
        FROM epg_programs
        WHERE channel_tvg_name = ? AND start <= ? AND stop > ?
        ORDER BY start DESC
        LIMIT 1
    """, (str(channel_id), now, now))
    row = c.fetchone()
    conn.close()
    if row:
        return JSONResponse({
            "title": row[0],
            "start": row[1],
            "stop": row[2],
            "description": row[3]
        })
    else:
        return JSONResponse({
            "title": "No Program",
            "start": "",
            "stop": "",
            "description": ""
        })