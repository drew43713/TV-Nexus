import os
import json
import shutil
from fastapi import APIRouter, Request, Form, HTTPException, UploadFile, File
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from .config import CONFIG_FILE_PATH, EPG_DIR, M3U_DIR
from .epg import parse_raw_epg_files, build_combined_epg

router = APIRouter()
templates = Jinja2Templates(directory="templates")

@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    # List EPG files.
    epg_files = sorted(
        [f for f in os.listdir(EPG_DIR) if f.lower().endswith((".xml", ".xmltv", ".gz"))]
    ) if os.path.exists(EPG_DIR) else []
    
    # Find the first (and only) M3U file.
    m3u_file = None
    if os.path.exists(M3U_DIR):
        for f in os.listdir(M3U_DIR):
            if f.lower().endswith(".m3u"):
                m3u_file = f
                break
    
    # Load current config (tuner count).
    try:
        with open(CONFIG_FILE_PATH, "r") as f:
            current_config = json.load(f)
    except Exception:
        current_config = {}
    tuner_count = current_config.get("TUNER_COUNT", 1)
    
    # Get query parameters for confirmation messages.
    updated = request.query_params.get("updated", None)
    epg_upload_success = request.query_params.get("epg_upload_success", None)
    m3u_upload_success = request.query_params.get("m3u_upload_success", None)
    parse_epg_success = request.query_params.get("parse_epg_success", None)
    
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "epg_files": epg_files,
        "m3u_file": m3u_file,
        "tuner_count": tuner_count,
        "updated": updated,
        "epg_upload_success": epg_upload_success,
        "m3u_upload_success": m3u_upload_success,
        "parse_epg_success": parse_epg_success,
    })

@router.post("/update_config")
def update_config(tuner_count: int = Form(...)):
    try:
        with open(CONFIG_FILE_PATH, "r") as f:
            current_config = json.load(f)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to load configuration.")
    
    current_config["TUNER_COUNT"] = tuner_count
    
    try:
        with open(CONFIG_FILE_PATH, "w") as f:
            json.dump(current_config, f, indent=4)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to save configuration.")
    
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
        build_combined_epg()
    except Exception as e:
        raise HTTPException(status_code=500, detail="Error re-parsing EPG files: " + str(e))
    
    # Return a JSON response so the UI can display a status message
    return {"success": True, "message": "EPG file uploaded and parsed successfully."}

@router.post("/upload_m3u")
async def upload_m3u(file: UploadFile = File(...)):
    allowed_ext = ".m3u"
    filename = file.filename
    ext = os.path.splitext(filename)[1].lower()
    if ext != allowed_ext:
        raise HTTPException(status_code=400, detail="Invalid file type. Only m3u files are allowed.")
    
    # Ensure the M3U directory exists and remove any existing m3u file.
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
    
    # Optionally, trigger loading the new m3u file immediately.
    from .m3u import load_m3u_files
    load_m3u_files()
    
    return RedirectResponse(url="/settings?m3u_upload_success=true", status_code=303)

@router.post("/parse_epg")
def parse_epg():
    """
    Endpoint to re-parse all raw EPG files and rebuild the combined EPG.
    Returns a JSON response with success and message keys.
    """
    try:
        parse_raw_epg_files()
        build_combined_epg()
        return {"success": True, "message": "EPG parsed successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/delete_epg")
def delete_epg(filename: str = Form(...)):
    """
    Endpoint to delete a specified EPG file and reparse the EPG data.
    Returns a JSON response so the UI can display a status message.
    """
    file_path = os.path.join(EPG_DIR, filename)
    if not os.path.exists(file_path):
        return {"success": False, "message": "EPG file not found."}
    try:
        os.remove(file_path)
    except Exception as e:
        return {"success": False, "message": "Error deleting file: " + str(e)}
    try:
        parse_raw_epg_files()
        build_combined_epg()
    except Exception as e:
        return {"success": False, "message": "Error re-parsing EPG files: " + str(e)}
    return {"success": True, "message": "EPG file deleted and EPG re-parsed successfully."}
