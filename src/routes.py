import os
import sqlite3
from fastapi import APIRouter, Request, HTTPException, Form
from fastapi.responses import (
    JSONResponse, FileResponse, PlainTextResponse, StreamingResponse,
    HTMLResponse, RedirectResponse
)
from fastapi.templating import Jinja2Templates
from .config import DB_FILE, MODIFIED_EPG_DIR
from .database import swap_channel_ids
from .epg import update_modified_epg
from .streaming import get_shared_stream, clear_shared_stream
from .m3u import load_m3u_files

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

@router.get("/lineup.json")
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
