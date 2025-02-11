from fastapi import APIRouter, Request, Form, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
import os
import json
import shutil
from .config import CONFIG_FILE_PATH, EPG_DIR
from .epg import parse_raw_epg_files, build_combined_epg

router = APIRouter()
from fastapi.templating import Jinja2Templates
templates = Jinja2Templates(directory="templates")

@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    # List M3U files.
    m3u_dir = os.path.join("config", "m3u")
    m3u_files = sorted([f for f in os.listdir(m3u_dir) if f.lower().endswith(".m3u")]) if os.path.exists(m3u_dir) else []
    
    # List EPG files.
    epg_dir = os.path.join("config", "epg")
    epg_files = sorted([f for f in os.listdir(epg_dir) if f.lower().endswith((".xml", ".xmltv", ".gz"))]) if os.path.exists(epg_dir) else []
    
    # Load current config (tuner count).
    try:
        with open(CONFIG_FILE_PATH, "r") as f:
            current_config = json.load(f)
    except Exception:
        current_config = {}
    tuner_count = current_config.get("TUNER_COUNT", 1)
    
    # Get query parameters for confirmation messages.
    updated = request.query_params.get("updated", None)
    upload_success = request.query_params.get("upload_success", None)
    
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "m3u_files": m3u_files,
        "epg_files": epg_files,
        "tuner_count": tuner_count,
        "updated": updated,
        "upload_success": upload_success
    })

@router.post("/update_config")
def update_config(tuner_count: int = Form(...)):
    # Load current config.
    try:
        with open(CONFIG_FILE_PATH, "r") as f:
            current_config = json.load(f)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to load configuration.")
    
    # Update tuner count.
    current_config["TUNER_COUNT"] = tuner_count
    
    try:
        with open(CONFIG_FILE_PATH, "w") as f:
            json.dump(current_config, f, indent=4)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to save configuration.")
    
    # Redirect back to /settings?updated=true so the banner is shown initially.
    return RedirectResponse(url="/settings?updated=true", status_code=303)

@router.post("/upload_epg")
async def upload_epg(file: UploadFile = File(...)):
    # Validate file extension.
    allowed_exts = [".xml", ".xmltv", ".gz"]
    filename = file.filename
    ext = os.path.splitext(filename)[1].lower()
    if ext not in allowed_exts:
        raise HTTPException(status_code=400, detail="Invalid file type. Allowed: xml, xmltv, gz")
    
    # Save file to EPG_DIR (replace existing file if necessary).
    destination = os.path.join(EPG_DIR, filename)
    try:
        with open(destination, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail="Could not save file: " + str(e))
    finally:
        file.file.close()
    
    # Reparse EPG files.
    try:
        parse_raw_epg_files()
        build_combined_epg()
    except Exception as e:
        raise HTTPException(status_code=500, detail="Error re-parsing EPG files: " + str(e))
    
    return RedirectResponse(url="/settings?upload_success=true", status_code=303)
