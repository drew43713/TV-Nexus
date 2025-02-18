import os
import json
import shutil
from fastapi import APIRouter, Request, Form, HTTPException, UploadFile, File
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from .config import CONFIG_FILE_PATH, M3U_DIR, EPG_DIR, MODIFIED_EPG_DIR, DB_FILE, LOGOS_DIR, CUSTOM_LOGOS_DIR, TUNER_COUNT, config
from .epg import parse_raw_epg_files, build_combined_epg, load_epg_color_mapping, save_epg_color_mapping, get_color_for_epg_file

router = APIRouter()
templates = Jinja2Templates(directory="templates")

@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    epg_files = sorted(
        [f for f in os.listdir(EPG_DIR) if f.lower().endswith((".xml", ".xmltv", ".gz"))]
    ) if os.path.exists(EPG_DIR) else []
    
    epg_colors = {}
    for file in epg_files:
        epg_colors[file] = get_color_for_epg_file(file)
    
    m3u_file = None
    if os.path.exists(M3U_DIR):
        for f in os.listdir(M3U_DIR):
            if f.lower().endswith(".m3u"):
                m3u_file = f
                break
    
    # Load current config
    try:
        with open(CONFIG_FILE_PATH, "r") as f:
            current_config = json.load(f)
    except Exception:
        current_config = {}
    
    tuner_count = current_config.get("TUNER_COUNT", 1)
    # Pull the REPARSE_EPG_INTERVAL from the config, default to 1440 if not present
    reparse_interval = current_config.get("REPARSE_EPG_INTERVAL", 1440)
    
    updated = request.query_params.get("updated", None)
    epg_upload_success = request.query_params.get("epg_upload_success", None)
    m3u_upload_success = request.query_params.get("m3u_upload_success", None)
    parse_epg_success = request.query_params.get("parse_epg_success", None)
    
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "epg_files": epg_files,
        "epg_colors": epg_colors,
        "m3u_file": m3u_file,
        "tuner_count": tuner_count,
        "reparse_interval": reparse_interval,  # Pass to template
        "updated": updated,
        "epg_upload_success": epg_upload_success,
        "m3u_upload_success": m3u_upload_success,
        "parse_epg_success": parse_epg_success,
    })

@router.post("/update_config")
def update_config(tuner_count: int = Form(...), reparse_epg_interval: int = Form(...)):
    """
    Updates TUNER_COUNT and REPARSE_EPG_INTERVAL in both the config.json file
    and the in-memory config dictionary so changes take effect without a restart.
    """
    # 1) Load the current config from disk
    try:
        with open(CONFIG_FILE_PATH, "r") as f:
            current_config = json.load(f)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to load configuration from disk.")

    # 2) Update the Python dictionary
    current_config["TUNER_COUNT"] = tuner_count
    current_config["REPARSE_EPG_INTERVAL"] = reparse_epg_interval

    # 3) Update our in-memory config as well (so background tasks see the new interval)
    config["TUNER_COUNT"] = tuner_count
    config["REPARSE_EPG_INTERVAL"] = reparse_epg_interval

    # 4) Write the updated config back to disk
    try:
        with open(CONFIG_FILE_PATH, "w") as f:
            json.dump(current_config, f, indent=4)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to save configuration to disk.")

    # 5) Redirect the user back to the settings page with a success message
    return RedirectResponse(url="/settings?updated=true", status_code=303)

@router.post("/upload_epg")
async def upload_epg(file: UploadFile = File(...)):
    allowed_exts = [".xml", ".xmltv", ".gz"]
    filename = file.filename
    ext = os.path.splitext(filename)[1].lower()
    if ext not in allowed_exts:
        raise HTTPException(status_code=400, detail="Invalid file type. Allowed: xml, xmltv, gz")
    
    destination = os.path.join(EPG_DIR, filename)
    try:
        with open(destination, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail="Could not save file: " + str(e))
    finally:
        file.file.close()
    
    try:
        parse_raw_epg_files()
        build_combined_epg()  # Keep the global rebuild after uploading a new EPG
    except Exception as e:
        raise HTTPException(status_code=500, detail="Error re-parsing EPG files: " + str(e))
    
    return {"success": True, "message": "EPG file uploaded and parsed successfully."}

@router.post("/upload_m3u")
async def upload_m3u(file: UploadFile = File(...)):
    allowed_ext = ".m3u"
    filename = file.filename
    ext = os.path.splitext(filename)[1].lower()
    if ext != allowed_ext:
        raise HTTPException(status_code=400, detail="Invalid file type. Only m3u files are allowed.")
    
    os.makedirs(M3U_DIR, exist_ok=True)
    for f in os.listdir(M3U_DIR):
        if f.lower().endswith(".m3u"):
            try:
                os.remove(os.path.join(M3U_DIR, f))
            except Exception as e:
                print(f"Error removing existing m3u file {f}: {e}")
    
    destination = os.path.join(M3U_DIR, filename)
    try:
        with open(destination, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail="Could not save file: " + str(e))
    finally:
        file.file.close()
    
    from .m3u import load_m3u_files
    load_m3u_files()
    
    return RedirectResponse(url="/settings?m3u_upload_success=true", status_code=303)

@router.post("/parse_epg")
def parse_epg():
    try:
        parse_raw_epg_files()
        build_combined_epg()  # Full rebuild on explicit user request
        return {"success": True, "message": "EPG parsed successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/delete_epg")
def delete_epg(filename: str = Form(...)):
    file_path = os.path.join(EPG_DIR, filename)
    if not os.path.exists(file_path):
        return {"success": False, "message": "EPG file not found."}
    try:
        os.remove(file_path)
    except Exception as e:
        return {"success": False, "message": "Error deleting file: " + str(e)}
    try:
        parse_raw_epg_files()
        build_combined_epg()  # Rebuild after removing an EPG file
    except Exception as e:
        return {"success": False, "message": "Error re-parsing EPG files: " + str(e)}
    return {"success": True, "message": "EPG file deleted and EPG re-parsed successfully."}

@router.post("/update_epg_color")
def update_epg_color(filename: str = Form(...), color: str = Form(...)):
    mapping = load_epg_color_mapping()
    mapping[filename] = color
    save_epg_color_mapping(mapping)
    return {"success": True, "message": "EPG file color updated."}
