import subprocess
import json
import sqlite3
import datetime
from fastapi import APIRouter, Request, Form, HTTPException, UploadFile, File, Query
from fastapi.responses import (
    JSONResponse, FileResponse, PlainTextResponse, StreamingResponse,
    HTMLResponse, RedirectResponse
)
import os
from .config import DB_FILE, MODIFIED_EPG_DIR, EPG_DIR, HOST_IP, PORT, CUSTOM_LOGOS_DIR, LOGOS_DIR, TUNER_COUNT, load_config
from .database import init_db, swap_channel_ids
from .epg import (
    update_modified_epg, update_channel_logo_in_epg, update_channel_metadata_in_epg,
    update_program_data_for_channel, parse_raw_epg_files, build_combined_epg
)
from .streaming import get_shared_stream, clear_shared_stream
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="templates")

def get_base_url():
    cfg = load_config()
    domain = cfg.get("DOMAIN_NAME", "").strip()
    if domain:
        return f"https://{domain}"
    else:
        return f"http://{HOST_IP}:{PORT}"

@router.get("/", response_class=HTMLResponse)
def web_interface(request: Request):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, name, url, tvg_name, logo_url, group_title, active FROM channels ORDER BY id")
    channels = c.fetchall()

    epg_map = {}
    epg_entry_map = {}
    stream_map = {}
    base_url = get_base_url()
    now = datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S") + " +0000"

    for ch in channels:
        ch_id, ch_name, ch_url, ch_tvg_name, ch_logo, ch_group, ch_active = ch
        c.execute("""
            SELECT title, start, stop, description 
            FROM epg_programs 
            WHERE channel_tvg_name = ? AND start <= ? AND stop > ?
            ORDER BY start DESC LIMIT 1
        """, (str(ch_id), now, now))
        program = c.fetchone()
        if program:
            epg_map[str(ch_id)] = {"title": program[0], "start": program[1], "stop": program[2], "description": program[3]}
        else:
            epg_map[str(ch_id)] = None
        epg_entry_map[str(ch_id)] = ch_tvg_name or ""
        stream_map[str(ch_id)] = f"{base_url}/tuner/{ch_id}"
    conn.close()

    js_script = ""
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
    config = load_config()
    tuner_count = config.get("TUNER_COUNT", 1)
    base_url = get_base_url()
    return JSONResponse({
        "FriendlyName": "TV Nexus",  # Updated friendly name
        "Manufacturer": "Custom",
        "ModelNumber": "TVN-1",      # New model number
        "FirmwareName": "tvnexus",   # Updated firmware name
        "FirmwareVersion": "20250802",
        "DeviceID": "12345678",
        "DeviceAuth": "testauth",
        "BaseURL": base_url,
        "LineupURL": f"{base_url}/lineup.json",
        "TunerCount": tuner_count
    })

@router.get("/lineup.json")
def lineup(request: Request):
    base_url = get_base_url()
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Only include channels that are active.
    c.execute("SELECT id, name, url, logo_url FROM channels WHERE active = 1")
    rows = c.fetchall()
    conn.close()

    lineup_data = []
    for ch in rows:
        ch_id, ch_name, ch_url, logo_url = ch
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
    return JSONResponse({
        "ScanInProgress": 0,
        "ScanPossible": 1,
        "Source": "Cable",
        "SourceList": ["Cable"]
    })

@router.get("/epg.xml")
def serve_epg():
    modified_epg_files = [os.path.join(MODIFIED_EPG_DIR, f)
                            for f in os.listdir(MODIFIED_EPG_DIR)
                            if f.endswith(".xml") or f.endswith(".xmltv")]
    if modified_epg_files:
        return FileResponse(modified_epg_files[0], media_type="application/xml")
    return PlainTextResponse("<tv></tv>", media_type="application/xml")

@router.get("/tuner/{channel_id}")
def tuner_stream(channel_id: int, request: Request):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT url FROM channels WHERE id=? AND active=1", (channel_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Channel not found or inactive.")
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
    clear_shared_stream(current_id)
    clear_shared_stream(new_id)
    return RedirectResponse(url="/", status_code=303)

# --- New endpoints for active/inactive state ---
@router.post("/update_channel_active")
def update_channel_active(channel_id: int = Form(...), active: bool = Form(...)):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE channels SET active = ? WHERE id = ?", (1 if active else 0, channel_id))
    conn.commit()
    conn.close()
    # Rebuild the combined EPG so that changes are reflected immediately.
    build_combined_epg()
    return JSONResponse({"success": True})

@router.post("/update_channels_active_bulk")
def update_channels_active_bulk(channel_ids: str = Form(...), active: bool = Form(...)):
    # Split the comma-separated list and filter out empty strings.
    ids = [cid.strip() for cid in channel_ids.split(',') if cid.strip()]
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Use executemany to update all channels in one transaction.
    data = [(1 if active else 0, cid) for cid in ids]
    c.executemany("UPDATE channels SET active = ? WHERE id = ?", data)
    conn.commit()
    conn.close()
    # Rebuild the combined EPG after the bulk update.
    build_combined_epg()
    return JSONResponse({"success": True})

@router.post("/update_channel_logo")
def update_channel_logo(channel_id: int = Form(...), new_logo: str = Form(...)):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE channels SET logo_url = ? WHERE id = ?", (new_logo, channel_id))
    conn.commit()
    conn.close()
    update_channel_logo_in_epg(channel_id, new_logo)
    return JSONResponse({"success": True})

@router.post("/update_channel_name")
def update_channel_name(channel_id: int = Form(...), new_name: str = Form(...)):
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

@router.get("/api/epg_entries")
def get_epg_entries(search: str = Query("", min_length=0)):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    if search:
        c.execute("""
            SELECT DISTINCT display_name
            FROM raw_epg_channels
            WHERE LOWER(display_name) LIKE LOWER(?)
            ORDER BY display_name
        """, (f"%{search.lower()}%",))
    else:
        c.execute("""
            SELECT DISTINCT display_name
            FROM raw_epg_channels
            ORDER BY display_name
        """)
    rows = c.fetchall()
    conn.close()
    results = [row[0] for row in rows if row[0]]
    return JSONResponse(results)

@router.post("/update_epg_entry")
def update_epg_entry(channel_id: int = Form(...), new_epg_entry: str = Form(...)):
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
    now = datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S") + " +0000"
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
        return JSONResponse({"title": row[0], "start": row[1], "stop": row[2], "description": row[3]})
    else:
        return JSONResponse({"title": "No Program", "start": "", "stop": "", "description": ""})

@router.get("/probe_stream")
def probe_stream(channel_id: int = Query(..., description="The channel ID to probe")):
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
        "ffprobe",
        "-v", "quiet",
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
    logos = []
    if os.path.exists(LOGOS_DIR):
        for f in os.listdir(LOGOS_DIR):
            if f.lower().endswith((".jpg", ".jpeg", ".png", ".gif")):
                logos.append(f"/static/logos/{f}")
    if os.path.exists(CUSTOM_LOGOS_DIR):
        for root, dirs, files in os.walk(CUSTOM_LOGOS_DIR):
            for f in files:
                if f.lower().endswith((".jpg", ".jpeg", ".png", ".gif")):
                    rel_path = os.path.relpath(os.path.join(root, f), CUSTOM_LOGOS_DIR)
                    rel_path = rel_path.replace("\\", "/")
                    logos.append(f"/custom_logos/{rel_path}")
    return JSONResponse(logos)

@router.post("/update_channel_properties")
def update_channel_properties(
    channel_id: int = Form(...),
    new_channel_number: int = Form(...),
    new_name: str = Form(...),
    new_category: str = Form(...),
    new_logo: str = Form(...),
    new_epg_entry: str = Form(...),
    new_active: int = Form(...)  # Accept active status (1 or 0)
):
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        # Retrieve the current epg entry and active status.
        c.execute("SELECT tvg_name, active FROM channels WHERE id = ?", (channel_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            return JSONResponse({"success": False, "error": "Channel not found."})
        old_epg_entry = row[0] if row[0] is not None else ""
        old_active = row[1]  # Current active status from the database.
        updated_channel_id = channel_id
        if channel_id != new_channel_number:
            swap = swap_channel_ids(channel_id, new_channel_number)
            update_modified_epg(channel_id, new_channel_number, swap)
            clear_shared_stream(channel_id)
            clear_shared_stream(new_channel_number)
            updated_channel_id = new_channel_number
        # Update the channel record with the new values, including the active status.
        c.execute("""
            UPDATE channels 
            SET name = ?, group_title = ?, logo_url = ?, tvg_name = ?, active = ?
            WHERE id = ?
        """, (new_name, new_category, new_logo, new_epg_entry, new_active, updated_channel_id))
        conn.commit()
        conn.close()
        # Regenerate EPG data if the EPG entry changed...
        if new_epg_entry != old_epg_entry:
            update_program_data_for_channel(updated_channel_id)
        else:
            update_channel_metadata_in_epg(updated_channel_id, new_name, new_logo)
        # AND regenerate the EPG if the channel was switched from inactive to active.
        if new_active == 1 and old_active == 0:
            update_program_data_for_channel(updated_channel_id)
        return JSONResponse({"success": True})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@router.post("/auto_number_channels")
def auto_number_channels(start_number: int = Form(...)):
    """

    Some code here to auto-number channels.

    """
