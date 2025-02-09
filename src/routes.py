import os
import gzip
import time
import sqlite3
import subprocess
import json
from fastapi import APIRouter, Request, HTTPException, Form, Query
from fastapi.responses import (
    JSONResponse, FileResponse, PlainTextResponse, StreamingResponse,
    HTMLResponse, RedirectResponse
)
from fastapi.templating import Jinja2Templates
from .config import DB_FILE, MODIFIED_EPG_DIR, EPG_DIR, HOST_IP, PORT, CUSTOM_LOGOS_DIR, LOGOS_DIR, TUNER_COUNT, load_config
from .database import swap_channel_ids
from .epg import update_modified_epg, update_channel_logo_in_epg, update_channel_metadata_in_epg, update_program_data_for_channel
from .streaming import get_shared_stream, clear_shared_stream
from .m3u import load_m3u_files
import xml.etree.ElementTree as ET
from datetime import datetime

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# Global cache variables.
cached_epg_entries = None
epg_entries_last_updated = 0
CACHE_DURATION_SECONDS = 300  # Cache for 5 minutes

# Define a base URL using the config file values.
BASE_URL = f"http://{HOST_IP}:{PORT}"

@router.get("/discover.json")
def discover(request: Request):
    config = load_config()  # Re-read configuration from file
    tuner_count = config.get("TUNER_COUNT", 1)
    base_url = f"http://{HOST_IP}:{PORT}"
    return JSONResponse({
        "FriendlyName": "IPTV HDHomeRun",
        "Manufacturer": "Custom",
        "ModelNumber": "HDTC-2US",
        "FirmwareName": "hdhomeruntc_atsc",
        "FirmwareVersion": "20250802",
        "DeviceID": "12345678",
        "DeviceAuth": "testauth",
        "BaseURL": base_url,
        "LineupURL": f"{base_url}/lineup.json",
        "TunerCount": tuner_count
    })

@router.get("/lineup.json")
def lineup(request: Request):
    # Use BASE_URL from config.
    base_url = BASE_URL
    
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, name, url, logo_url FROM channels")
    rows = c.fetchall()
    conn.close()
    
    lineup_data = []
    for ch_id, ch_name, ch_url, logo_url in rows:
        # If the logo URL is relative, build the full URL.
        if logo_url and logo_url.startswith("/"):
            full_logo_url = f"{base_url}{logo_url}"
        else:
            full_logo_url = logo_url
        guide_number = str(ch_id)
        channel_obj = {
            "GuideNumber": guide_number,
            "GuideName": ch_name,
            "Station": guide_number,
            "Logo": full_logo_url if full_logo_url else "",
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

@router.get("/web", response_class=HTMLResponse)
def web_interface(request: Request):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, name, url, tvg_name, logo_url, group_title FROM channels ORDER BY id")
    channels = c.fetchall()

    epg_map = {}
    epg_entry_map = {}  # Store which EPG entry is assigned to each channel
    stream_map = {}  # Store the stream URL for each channel

    # Use BASE_URL from config.
    base_url = BASE_URL
    
    # Format the current UTC time as in the database.  
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

        # Generate the stream URL using BASE_URL.
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
        "stream_map": stream_map,
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
    This version scans only the raw EPG files and includes only channels
    that have at least one associated programme.
    Results are cached to improve efficiency.
    """
    global cached_epg_entries, epg_entries_last_updated
    current_time = time.time()
    # Return cached result if within the cache duration.
    if cached_epg_entries is not None and (current_time - epg_entries_last_updated) < CACHE_DURATION_SECONDS:
        return JSONResponse(sorted(list(cached_epg_entries)))
    
    epg_entries = set()
    
    def process_epg_root(root):
        """
        Process an XML root from an EPG file. Build a mapping of channel IDs
        to display names and record those channel IDs that have at least one programme.
        Only channels with programmes are added to the global epg_entries set.
        """
        channels_map = {}
        # Build a mapping from channel id to display name.
        for ch in root.findall("channel"):
            disp_el = ch.find("display-name")
            if disp_el is not None and disp_el.text:
                channels_map[ch.get("id")] = disp_el.text.strip()
        channels_with_programmes = set()
        # Collect channel ids that appear in any programme element.
        for prog in root.findall("programme"):
            chan = prog.get("channel")
            if chan:
                channels_with_programmes.add(chan)
        # Add the display name only if there is at least one programme for the channel.
        for cid, dname in channels_map.items():
            if cid in channels_with_programmes:
                epg_entries.add(dname)
    
    # Iterate over all raw EPG files.
    for filename in os.listdir(EPG_DIR):
        if filename.lower().endswith((".xml", ".xmltv", ".gz")):
            file_path = os.path.join(EPG_DIR, filename)
            try:
                with open(file_path, "rb") as f_obj:
                    # Read first two bytes to check for gzip signature.
                    magic = f_obj.read(2)
                    f_obj.seek(0)
                    if magic == b'\x1f\x8b':
                        # Process gzipped file.
                        with gzip.open(f_obj, "rb") as gz_obj:
                            decompressed_data = gz_obj.read()
                        root = ET.fromstring(decompressed_data)
                    else:
                        # Process a normal XML file.
                        tree = ET.parse(f_obj)
                        root = tree.getroot()
                process_epg_root(root)
            except Exception as e:
                print(f"Error processing raw EPG file {file_path}: {e}")
    
    # Cache and return the sorted list of entries.
    cached_epg_entries = epg_entries
    epg_entries_last_updated = current_time
    return JSONResponse(sorted(list(epg_entries)))

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

@router.get("/probe_stream")
def probe_stream(channel_id: int = Query(..., description="The channel ID to probe")):
    """
    Probe the stream for a given channel using ffprobe and return technical information.
    """
    # Lookup the channel URL from the database.
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT url FROM channels WHERE id = ?", (channel_id,))
    row = c.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Channel not found.")
    
    stream_url = row[0]
    if not stream_url:
        raise HTTPException(status_code=400, detail="Invalid stream URL for channel.")

    # Build the ffprobe command.
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        stream_url
    ]
    try:
        # Run ffprobe and capture the output.
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        output = result.stdout.decode("utf-8")
        ffprobe_data = json.loads(output)
        return JSONResponse(ffprobe_data)
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"ffprobe error: {e.stderr.decode('utf-8')}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/logos")
def get_logos():
    """
    Return a JSON list of available logos (from both cached and custom folders).
    For custom logos, recursively search all subdirectories.
    """
    logos = []
    # List logos from the cached logos directory.
    if os.path.exists(LOGOS_DIR):
        for f in os.listdir(LOGOS_DIR):
            if f.lower().endswith((".jpg", ".jpeg", ".png", ".gif")):
                logos.append(f"/static/logos/{f}")
    # List logos from the custom logos directory recursively.
    if os.path.exists(CUSTOM_LOGOS_DIR):
        for root, dirs, files in os.walk(CUSTOM_LOGOS_DIR):
            for f in files:
                if f.lower().endswith((".jpg", ".jpeg", ".png", ".gif")):
                    # Compute the path relative to the CUSTOM_LOGOS_DIR.
                    rel_path = os.path.relpath(os.path.join(root, f), CUSTOM_LOGOS_DIR)
                    # Normalize the path separator to '/' so it works in a URL.
                    rel_path = rel_path.replace("\\", "/")
                    logos.append(f"/static/custom_logos/{rel_path}")
    return JSONResponse(logos)

@router.post("/update_channel_logo")
def update_channel_logo(channel_id: int = Form(...), new_logo: str = Form(...)):
    """
    Update a channel's logo in the database so the change is persistent.
    """
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE channels SET logo_url = ? WHERE id = ?", (new_logo, channel_id))
    conn.commit()
    conn.close()

    update_channel_logo_in_epg(channel_id, new_logo)

    return JSONResponse({"success": True})

@router.post("/update_channel_name")
def update_channel_name(channel_id: int = Form(...), new_name: str = Form(...)):
    """
    Update the channel name for the given channel_id.
    Expects a form with 'channel_id' and 'new_name'.
    Returns a JSON response indicating success.
    """
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        
        # Verify that the channel exists.
        c.execute("SELECT id FROM channels WHERE id = ?", (channel_id,))
        if not c.fetchone():
            conn.close()
            raise HTTPException(status_code=404, detail="Channel not found.")
        
        # Update the channel name in the database.
        c.execute("UPDATE channels SET name = ? WHERE id = ?", (new_name, channel_id))
        conn.commit()
        conn.close()
        
        return JSONResponse({"success": True})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/update_channel_category")
def update_channel_category(channel_id: int = Form(...), new_category: str = Form(...)):
    """
    Update the channel category for the given channel_id.
    Expects a form with 'channel_id' and 'new_category'.
    Returns a JSON response indicating success.
    """
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        
        # Verify that the channel exists.
        c.execute("SELECT id FROM channels WHERE id = ?", (channel_id,))
        if not c.fetchone():
            conn.close()
            raise HTTPException(status_code=404, detail="Channel not found.")
        
        # Update the channel category in the database (assuming the column is named group_title).
        c.execute("UPDATE channels SET group_title = ? WHERE id = ?", (new_category, channel_id))
        conn.commit()
        conn.close()
        
        return JSONResponse({"success": True})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/categories")
def get_categories():
    """
    Retrieve a list of available categories by selecting distinct group_title values from the channels table.
    Returns a JSON list.
    """
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT group_title FROM channels")
        rows = cursor.fetchall()
        conn.close()
        # Filter out None or empty values and extract the category text.
        categories = [row[0] for row in rows if row[0]]
        return JSONResponse(categories)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/update_channel_properties")
def update_channel_properties(
    channel_id: int = Form(...),
    new_channel_number: int = Form(...),
    new_name: str = Form(...),
    new_category: str = Form(...),
    new_logo: str = Form(...),
    new_epg_entry: str = Form(...)
):
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        # Get the current EPG entry (tvg_name) for this channel.
        c.execute("SELECT tvg_name FROM channels WHERE id = ?", (channel_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            return JSONResponse({"success": False, "error": "Channel not found."})
        old_epg_entry = row[0] if row[0] is not None else ""

        # If the channel number has changed, perform the swap logic.
        updated_channel_id = channel_id
        if channel_id != new_channel_number:
            swap = swap_channel_ids(channel_id, new_channel_number)
            update_modified_epg(channel_id, new_channel_number, swap)
            clear_shared_stream(channel_id)
            clear_shared_stream(new_channel_number)
            updated_channel_id = new_channel_number

        # Update channel properties (name, category, logo, and EPG entry) in the database.
        c.execute(
            "UPDATE channels SET name = ?, group_title = ?, logo_url = ?, tvg_name = ? WHERE id = ?",
            (new_name, new_category, new_logo, new_epg_entry, updated_channel_id)
        )
        conn.commit()
        conn.close()

        # If the EPG entry (tvg_name) has changed, reparse the raw EPG files for that channel;
        # otherwise, just update the metadata in the modified EPG file.
        if new_epg_entry != old_epg_entry:
            update_program_data_for_channel(updated_channel_id)
        else:
            update_channel_metadata_in_epg(updated_channel_id, new_name, new_logo)

        return JSONResponse({"success": True})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})