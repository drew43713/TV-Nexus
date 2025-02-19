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
from .database import init_db, swap_channel_numbers
from .epg import (
    update_modified_epg, update_channel_logo_in_epg, update_channel_metadata_in_epg,
    update_program_data_for_channel, parse_raw_epg_files, build_combined_epg, load_epg_color_mapping
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
    import datetime
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, channel_number, name, url, tvg_name, logo_url, group_title, active FROM channels ORDER BY channel_number")
    channels = c.fetchall()

    epg_map = {}
    epg_entry_map = {}
    stream_map = {}
    base_url = get_base_url()
    now = datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S") + " +0000"

    for channel in channels:
        ch_id, ch_number, ch_name, ch_url, ch_tvg_name, ch_logo, ch_group, ch_active = channel
        c.execute("""
            SELECT title, start, stop, description 
              FROM epg_programs 
             WHERE channel_tvg_name = ? AND start <= ? AND stop > ?
             ORDER BY start DESC LIMIT 1
        """, (str(ch_number), now, now))
        program = c.fetchone()
        if program:
            epg_map[str(ch_number)] = {
                "title": program[0],
                "start": program[1],
                "stop": program[2],
                "description": program[3]
            }
        else:
            epg_map[str(ch_number)] = None

        epg_entry_map[str(ch_number)] = ch_tvg_name or ""
        stream_map[str(ch_number)] = f"{base_url}/tuner/{ch_number}"
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
        "FriendlyName": "TV Nexus",
        "Manufacturer": "Custom",
        "ModelNumber": "TVN-1",
        "FirmwareName": "tvnexus",
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
    c.execute("SELECT channel_number, name, url, logo_url FROM channels WHERE active = 1 ORDER BY channel_number")
    rows = c.fetchall()
    conn.close()

    lineup_data = []
    for ch in rows:
        channel_number, ch_name, ch_url, logo_url = ch
        channel_str = str(channel_number)
        if logo_url and logo_url.startswith("/"):
            full_logo_url = f"{base_url}{logo_url}"
        else:
            full_logo_url = logo_url or ""
        channel_obj = {
            "GuideNumber": channel_str,
            "GuideName": ch_name,
            "Station": channel_str,
            "Logo": full_logo_url,
            "URL": f"{base_url}/tuner/{channel_str}"
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


@router.get("/tuner/{channel_number}")
def tuner_stream(channel_number: int):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT url FROM channels WHERE channel_number=? AND active=1", (channel_number,))
    row = c.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Channel not found or inactive.")
    stream_url = row[0]
    if not stream_url:
        raise HTTPException(status_code=404, detail="Invalid channel URL.")

    shared = get_shared_stream(channel_number, stream_url)
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
                    shared_streams.pop(channel_number, None)
    return StreamingResponse(streamer(), media_type="video/mp2t")


@router.post("/update_channel_number")
def update_channel_number(current_id: int = Form(...), new_id: int = Form(...)):
    """
    current_id and new_id here are actually channel_numbers, not DB IDs.
    We'll do the swap in the DB, then call update_modified_epg.
    """
    if current_id == new_id:
        return RedirectResponse(url="/", status_code=303)
    swap = swap_channel_numbers(current_id, new_id)
    
    # CHANGED: Immediately update the EPG.xml references
    update_modified_epg(current_id, new_id, swap)
    
    return RedirectResponse(url="/", status_code=303)


@router.post("/update_channel_active")
def update_channel_active(channel_id: int = Form(...), active: bool = Form(...)):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE channels SET active = ? WHERE id = ?", (1 if active else 0, channel_id))
    conn.commit()
    conn.close()

    # If activating, partial re-parse so the channel gets EPG:
    if active:
        update_program_data_for_channel(channel_id)
    return JSONResponse({"success": True})


@router.post("/update_channels_active_bulk")
def update_channels_active_bulk(channel_ids: str = Form(...), active: bool = Form(...)):
    ids = [cid.strip() for cid in channel_ids.split(',') if cid.strip()]
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    data = [(1 if active else 0, cid) for cid in ids]
    c.executemany("UPDATE channels SET active = ? WHERE id = ?", data)
    conn.commit()
    conn.close()

    # Partial EPG update for each newly activated channel
    if active:
        for channel_id in ids:
            update_program_data_for_channel(int(channel_id))
    
    return JSONResponse({"success": True})


@router.post("/update_channel_logo")
def update_channel_logo(channel_id: int = Form(...), new_logo: str = Form(...)):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE channels SET logo_url = ? WHERE id = ?", (new_logo, channel_id))
    conn.commit()
    conn.close()

    # Reflect this new logo in the EPG.xml
    update_channel_logo_in_epg(channel_id, new_logo)
    return JSONResponse({"success": True})


@router.post("/update_channel_name")
def update_channel_name(channel_id: int = Form(...), new_name: str = Form(...)):
    """
    If you want the changed name to appear in EPG.xml <channel display-name="...">,
    we must also call update_channel_metadata_in_epg with the new name.
    We'll fetch the channel's current logo so it doesn't get lost.
    """
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT logo_url FROM channels WHERE id = ?", (channel_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            raise HTTPException(status_code=404, detail="Channel not found.")
        existing_logo = row[0] or ""
        
        c.execute("UPDATE channels SET name = ? WHERE id = ?", (new_name, channel_id))
        conn.commit()
        conn.close()

        # CHANGED: Update the EPG <channel> node to reflect the new name
        update_channel_metadata_in_epg(channel_id, new_name, existing_logo)
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
def get_epg_entries(search: str = Query("", min_length=0), raw_file: str = Query("", min_length=0)):
    """
    Returns an array of objects, each having:
      {
        "display_name": "...",
        "raw_epg_file": "...",
        "color": "#FFFFFF"
      }
    If 'search' is provided, we filter by display_name.
    If 'raw_file' is provided, we also filter by raw_epg_file.
    """

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # Build a dynamic WHERE clause to handle search and raw_file.
    where_clauses = []
    params = []

    if search:
        where_clauses.append("LOWER(rec.display_name) LIKE LOWER(?)")
        params.append(f"%{search.lower()}%")

    if raw_file:
        where_clauses.append("rep.raw_epg_file = ?")
        params.append(raw_file)

    # Base query with join so we can retrieve raw_epg_file
    query = """
        SELECT DISTINCT rec.display_name, rep.raw_epg_file
        FROM raw_epg_channels rec
        JOIN raw_epg_programs rep ON rep.raw_channel_id = rec.raw_id
    """

    # If we have any filters, append WHERE
    if where_clauses:
        query += " WHERE " + " AND ".join(where_clauses)

    query += " ORDER BY rec.display_name"

    c.execute(query, params)
    rows = c.fetchall()
    conn.close()

    # Load your EPG color mapping, e.g. { "EPG1.xml": "#ffcc00", ... }
    color_map = load_epg_color_mapping()

    # Build an array of objects
    results = []
    for (display_name, raw_epg_file) in rows:
        if not display_name:
            continue
        raw_epg_file = raw_epg_file or ""
        color = color_map.get(raw_epg_file, "#ffffff")
        results.append({
            "display_name": display_name,
            "raw_epg_file": raw_epg_file,
            "color": color
        })

    return JSONResponse(results)

@router.post("/update_epg_entry")
def update_epg_entry(channel_id: int = Form(...), new_epg_entry: str = Form(...)):
    """
    Allows changing the tvg_name for a single channel, then partial re-parse.
    """
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT tvg_name FROM channels WHERE id = ?", (channel_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            return JSONResponse({"success": False, "error": "Channel not found."})
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
    new_active: int = Form(...)  # 1 for active, 0 for inactive
):
    """
    High-level "edit channel" endpoint:
      - possibly swap channel_number
      - update name, category, logo, tvg_name, active
      - update partial EPG if needed
      - update channel_name or channel_number in EPG.xml
    """
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT tvg_name, active, channel_number, logo_url, name FROM channels WHERE id = ?", (channel_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            return JSONResponse({"success": False, "error": "Channel not found."})
        old_epg_entry = row[0] if row[0] else ""
        old_active = row[1]
        old_channel_number = row[2]
        old_logo = row[3] if row[3] else ""
        old_name = row[4] if row[4] else ""

        # If the channel_number changed, do the swap
        updated_channel_number = old_channel_number
        swap = False
        if old_channel_number != new_channel_number:
            swap = swap_channel_numbers(old_channel_number, new_channel_number)
            updated_channel_number = new_channel_number
            # CHANGED: reflect this in EPG.xml
            update_modified_epg(old_channel_number, new_channel_number, swap)

        # Update the channel record
        c.execute("""
            UPDATE channels
               SET name = ?,
                   group_title = ?,
                   logo_url = ?,
                   tvg_name = ?,
                   active = ?,
                   channel_number = ?
             WHERE id = ?
        """, (new_name, new_category, new_logo, new_epg_entry, new_active, updated_channel_number, channel_id))
        conn.commit()
        conn.close()

        # If the user changed the channel name or logo, reflect in EPG.xml
        if new_name != old_name or new_logo != old_logo:
            update_channel_metadata_in_epg(channel_id, new_name, new_logo)

        # If user changed EPG entry or turned the channel active, do partial re-parse
        if new_epg_entry != old_epg_entry or (new_active == 1 and old_active == 0):
            update_program_data_for_channel(channel_id)

        return JSONResponse({"success": True})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/auto_number_channels")
def auto_number_channels(
    start_number: int = Form(...),
    channel_ids: str = Form(...)
):
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()

        filtered_ids = [int(x.strip()) for x in channel_ids.split(",") if x.strip()]
        if not filtered_ids:
            return JSONResponse({"success": False, "message": "No channels provided."})

        n = len(filtered_ids)
        target_min = start_number
        target_max = start_number + n - 1
        TEMP_OFFSET = 1000000

        # Step 1: Temporarily assign filtered channels a negative value
        for ch_id in filtered_ids:
            temp_value = -abs(ch_id)
            c.execute("UPDATE channels SET channel_number = ? WHERE id = ?", (temp_value, ch_id))

        # Step 2: Bump unfiltered channels if they conflict
        placeholders = ",".join("?" for _ in filtered_ids)
        c.execute(f"SELECT id, channel_number FROM channels WHERE id NOT IN ({placeholders})", filtered_ids)
        unfiltered_channels = c.fetchall()
        for uc_id, uc_num in unfiltered_channels:
            if target_min <= uc_num <= target_max:
                new_num = uc_num + TEMP_OFFSET
                c.execute("UPDATE channels SET channel_number = ? WHERE id = ?", (new_num, uc_id))

        # Step 3: Assign final sequential numbers
        for i, ch_id in enumerate(filtered_ids):
            final_number = start_number + i
            c.execute("UPDATE channels SET channel_number = ? WHERE id = ?", (final_number, ch_id))

        conn.commit()
        conn.close()

        # If you want a global rebuild after a mass renumber, do it here:
        # build_combined_epg()

        return JSONResponse({"success": True, "message": "Filtered channels renumbered successfully."})
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)})


@router.get("/api/epg_filenames")
def get_epg_filenames():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        SELECT DISTINCT raw_epg_file
          FROM raw_epg_programs
         WHERE raw_epg_file IS NOT NULL AND raw_epg_file != ''
         ORDER BY raw_epg_file
    """)
    rows = c.fetchall()
    conn.close()
    filenames = [row[0] for row in rows]
    return JSONResponse(filenames)
