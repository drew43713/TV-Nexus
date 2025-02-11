import os
import sqlite3
import time
import gzip
import subprocess
import json
from datetime import datetime

from fastapi import APIRouter, Request, HTTPException, Form, Query
from fastapi.responses import (
    JSONResponse, FileResponse, PlainTextResponse, StreamingResponse,
    HTMLResponse, RedirectResponse
)

from fastapi.templating import Jinja2Templates

from .config import (
    DB_FILE, MODIFIED_EPG_DIR, EPG_DIR, HOST_IP, PORT, CUSTOM_LOGOS_DIR,
    LOGOS_DIR, TUNER_COUNT, load_config
)
from .database import swap_channel_ids
from .epg import (
    update_modified_epg, update_channel_logo_in_epg, update_channel_metadata_in_epg,
    update_program_data_for_channel
)
from .streaming import get_shared_stream, clear_shared_stream
from .m3u import load_m3u_files

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# Global cache variables.
cached_epg_entries = None
epg_entries_last_updated = 0
CACHE_DURATION_SECONDS = 300  # Cache for 5 minutes

# ------------------------------------------------------
# A helper to pick the correct base URL
# ------------------------------------------------------
def get_base_url():
    """
    If DOMAIN_NAME is present in the config, return https://DOMAIN_NAME,
    otherwise fallback to http://HOST_IP:PORT
    """
    cfg = load_config()
    domain = cfg.get("DOMAIN_NAME", "").strip()
    if domain:
        return f"https://{domain}"
    else:
        return f"http://{HOST_IP}:{PORT}"


@router.get("/", response_class=HTMLResponse)
def web_interface(request: Request):
    """
    This was previously /web. Now served at the root path '/'.
    Renders index.html with the list of channels.
    """
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, name, url, tvg_name, logo_url, group_title FROM channels ORDER BY id")
    channels = c.fetchall()

    epg_map = {}
    epg_entry_map = {}
    stream_map = {}

    # We'll call get_base_url() to build link prefixes
    base_url = get_base_url()

    # Format current UTC time in your usual EPG format
    now = datetime.utcnow().strftime("%Y%m%d%H%M%S") + " +0000"

    for ch_id, ch_name, ch_url, ch_tvg_name, ch_logo, ch_group in channels:
        # Fetch current EPG program
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

        epg_entry_map[str(ch_id)] = ch_tvg_name or ""
        # Construct the tuner stream URL
        stream_map[str(ch_id)] = f"{base_url}/tuner/{ch_id}"

    conn.close()

    js_script = """
    <script>
        // (optional extra JS can be placed here)
    </script>
    """

    return templates.TemplateResponse("index.html", {
        "request": request,
        "channels": channels,
        "epg_map": epg_map,
        "epg_entry_map": epg_entry_map,
        "stream_map": stream_map,
        "BASE_URL": base_url,
        "js_script": js_script
    })


@router.get("/discover.json")
def discover(request: Request):
    """
    Returns the HDHomeRun-like discover.json. 
    TunerCount uses the config. 
    The BaseURL references get_base_url().
    """
    config = load_config()
    tuner_count = config.get("TUNER_COUNT", 1)
    base_url = get_base_url()  # new approach

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
    """
    The JSON lineup for HDHomeRun. Streams point to /tuner/{channel_id}.
    Logo URLs are also built using get_base_url().
    """
    base_url = get_base_url()

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, name, url, logo_url FROM channels")
    rows = c.fetchall()
    conn.close()

    lineup_data = []
    for ch_id, ch_name, ch_url, logo_url in rows:
        # If the logo URL is relative, build the full URL
        if logo_url and logo_url.startswith("/"):
            full_logo_url = f"{base_url}{logo_url}"
        else:
            full_logo_url = logo_url or ""
        guide_number = str(ch_id)
        channel_obj = {
            "GuideNumber": guide_number,
            "GuideName": ch_name,
            "Station": guide_number,
            "Logo": full_logo_url,
            "URL": f"{base_url}/tuner/{ch_id}"
        }
        lineup_data.append(channel_obj)

    return JSONResponse(lineup_data)


@router.get("/lineup_status.json")
def lineup_status():
    """
    Another HDHomeRun endpoint to indicate lineup scanning status.
    """
    return JSONResponse({
        "ScanInProgress": 0,
        "ScanPossible": 1,
        "Source": "Cable",
        "SourceList": ["Cable"]
    })


@router.get("/epg.xml")
def serve_epg():
    """
    Serves the combined EPG.xml from MODIFIED_EPG_DIR, if present.
    """
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
    """
    Streams a channel via a shared FFmpeg process. 
    The URL is constructed in lineup.json using get_base_url().
    """
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


@router.post("/update_channel_number")
def update_channel_number(current_id: int = Form(...), new_id: int = Form(...)):
    if current_id == new_id:
        return RedirectResponse(url="/", status_code=303)
    swap = swap_channel_ids(current_id, new_id)
    update_modified_epg(current_id, new_id, swap)
    # Clear any active shared streams for these channel IDs
    clear_shared_stream(current_id)
    clear_shared_stream(new_id)
    return RedirectResponse(url="/", status_code=303)


@router.get("/api/epg_entries")
def get_epg_entries(search: str = Query("", min_length=0)):
    """
    Return a list of EPG channel names from the `epg_channels` table.
    If ?search= is provided, do a LIKE filter. Limit result to 100.
    """
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    if search:
        c.execute("""
            SELECT name FROM epg_channels
            WHERE LOWER(name) LIKE LOWER(?)
            ORDER BY name
            LIMIT 100
        """, (f"%{search}%",))
    else:
        c.execute("SELECT name FROM epg_channels ORDER BY name LIMIT 100")
    rows = c.fetchall()
    conn.close()
    results = [row[0] for row in rows]
    return JSONResponse(results)


@router.post("/update_epg_entry")
def update_epg_entry(channel_id: int = Form(...), new_epg_entry: str = Form(...)):
    """
    Update the channel's tvg_name in the database and rebuild only that channel's EPG.
    """
    try:
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

        update_program_data_for_channel(channel_id)

        return JSONResponse({"success": True})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/api/current_program")
def get_current_program(channel_id: int):
    """
    Returns the current programme for a given channel based on epg_programs table.
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
    Probe the stream for a given channel using ffprobe and return technical info.
    """
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

    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        stream_url
    ]
    try:
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
    Return a JSON list of available logos from both /static/logos and /custom_logos.
    """
    logos = []
    # static logos
    if os.path.exists(LOGOS_DIR):
        for f in os.listdir(LOGOS_DIR):
            if f.lower().endswith((".jpg", ".jpeg", ".png", ".gif")):
                logos.append(f"/static/logos/{f}")
    # custom logos
    if os.path.exists(CUSTOM_LOGOS_DIR):
        for root, dirs, files in os.walk(CUSTOM_LOGOS_DIR):
            for f in files:
                if f.lower().endswith((".jpg", ".jpeg", ".png", ".gif")):
                    rel_path = os.path.relpath(os.path.join(root, f), CUSTOM_LOGOS_DIR)
                    rel_path = rel_path.replace("\\", "/")
                    logos.append(f"/custom_logos/{rel_path}")
    return JSONResponse(logos)


@router.post("/update_channel_logo")
def update_channel_logo(channel_id: int = Form(...), new_logo: str = Form(...)):
    """
    Update a channel's logo in the DB and EPG.xml.
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
    Update the channel name in DB, does not re-parse EPG.
    """
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT id FROM channels WHERE id = ?", (channel_id,))
        if not c.fetchone():
            conn.close()
            raise HTTPException(status_code=404, detail="Channel not found.")

        c.execute("UPDATE channels SET name = ? WHERE id = ?", (new_name, channel_id))
        conn.commit()
        conn.close()
        return JSONResponse({"success": True})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/update_channel_category")
def update_channel_category(channel_id: int = Form(...), new_category: str = Form(...)):
    """
    Update the channel category for the given channel_id in DB.
    """
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT id FROM channels WHERE id = ?", (channel_id,))
        if not c.fetchone():
            conn.close()
            raise HTTPException(status_code=404, detail="Channel not found.")

        c.execute("UPDATE channels SET group_title = ? WHERE id = ?", (new_category, channel_id))
        conn.commit()
        conn.close()

        return JSONResponse({"success": True})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/categories")
def get_categories():
    """
    Retrieve distinct category names from channels.group_title.
    """
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT group_title FROM channels")
        rows = cursor.fetchall()
        conn.close()
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
    """
    Single form to update multiple channel properties.
    If channel_number changes, do the swap logic. Then update
    name, category, logo, EPG, etc.
    """
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT tvg_name FROM channels WHERE id = ?", (channel_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            return JSONResponse({"success": False, "error": "Channel not found."})
        old_epg_entry = row[0] if row[0] is not None else ""

        updated_channel_id = channel_id
        if channel_id != new_channel_number:
            swap = swap_channel_ids(channel_id, new_channel_number)
            update_modified_epg(channel_id, new_channel_number, swap)
            clear_shared_stream(channel_id)
            clear_shared_stream(new_channel_number)
            updated_channel_id = new_channel_number

        c.execute("""
            UPDATE channels 
            SET name = ?, group_title = ?, logo_url = ?, tvg_name = ?
            WHERE id = ?
        """, (new_name, new_category, new_logo, new_epg_entry, updated_channel_id))
        conn.commit()
        conn.close()

        if new_epg_entry != old_epg_entry:
            update_program_data_for_channel(updated_channel_id)
        else:
            update_channel_metadata_in_epg(updated_channel_id, new_name, new_logo)

        return JSONResponse({"success": True})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})
