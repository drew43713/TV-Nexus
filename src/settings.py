import os
import json
import shutil
import shlex
import asyncio
from fastapi import APIRouter, Request, Form, HTTPException, UploadFile, File
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from .config import CONFIG_FILE_PATH, M3U_DIR, EPG_DIR, MODIFIED_EPG_DIR, DB_FILE, LOGOS_DIR, CUSTOM_LOGOS_DIR, TUNER_COUNT, config
from .epg import parse_raw_epg_files, build_combined_epg, load_epg_color_mapping, save_epg_color_mapping, get_color_for_epg_file
from .tasks import start_epg_reparse_task

from .streaming import (
    list_ffmpeg_profiles,
    get_ffmpeg_profiles,
    select_ffmpeg_profile,
    register_ffmpeg_profile,
    delete_ffmpeg_profile,
    get_selected_ffmpeg_profile_name,
)

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

    # Ensure URL_SCHEME exists and is normalized for the template
    scheme = str(current_config.get("URL_SCHEME", "http")).strip().lower()
    if scheme not in ("http", "https"):
        scheme = "http"
    current_config["URL_SCHEME"] = scheme
    
    tuner_count = current_config.get("TUNER_COUNT", 1)
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
        "reparse_interval": reparse_interval,
        "updated": updated,
        "epg_upload_success": epg_upload_success,
        "m3u_upload_success": m3u_upload_success,
        "parse_epg_success": parse_epg_success,
        "config": current_config  # Pass the config here!
    })

@router.post("/update_config")
async def update_config(
    HOST_IP: str = Form(...),
    PORT: int = Form(...),
    M3U_DIR: str = Form(...),
    EPG_DIR: str = Form(...),
    MODIFIED_EPG_DIR: str = Form(...),
    DB_FILE: str = Form(...),
    LOGOS_DIR: str = Form(...),
    CUSTOM_LOGOS_DIR: str = Form(...),
    TUNER_COUNT: int = Form(...),
    DOMAIN_NAME: str = Form(...),
    URL_SCHEME: str = Form(...),
    EPG_COLORS_FILE: str = Form(...),
    REPARSE_EPG_INTERVAL: int = Form(...)
):
    """
    Update all configuration values from the settings page.
    """
    try:
        with open(CONFIG_FILE_PATH, "r") as f:
            current_config = json.load(f)
    except Exception:
        current_config = {}

    # Normalize URL_SCHEME from form input
    scheme = str(URL_SCHEME).strip().lower()
    if scheme not in ("http", "https"):
        scheme = "http"
    
    # Update the config dictionary with new values.
    current_config.update({
        "HOST_IP": HOST_IP,
        "PORT": PORT,
        "M3U_DIR": M3U_DIR,
        "EPG_DIR": EPG_DIR,
        "MODIFIED_EPG_DIR": MODIFIED_EPG_DIR,
        "DB_FILE": DB_FILE,
        "LOGOS_DIR": LOGOS_DIR,
        "CUSTOM_LOGOS_DIR": CUSTOM_LOGOS_DIR,
        "TUNER_COUNT": TUNER_COUNT,
        "DOMAIN_NAME": DOMAIN_NAME,
        "URL_SCHEME": scheme,
        "EPG_COLORS_FILE": EPG_COLORS_FILE,
        "REPARSE_EPG_INTERVAL": REPARSE_EPG_INTERVAL
    })
    
    try:
        with open(CONFIG_FILE_PATH, "w") as f:
            json.dump(current_config, f, indent=4)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to save configuration.")
    
    # Update the in-memory configuration.
    config.update(current_config)
    
    # Restart the background EPG re-parse task.
    await start_epg_reparse_task()
    
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
    
    return {"success": True, "message": "M3U file uploaded successfully."}

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


@router.get("/api/ffmpeg/profiles", response_class=JSONResponse)
def api_list_ffmpeg_profiles():
    profiles = get_ffmpeg_profiles()
    selected = get_selected_ffmpeg_profile_name()

    # Load persisted custom args strings to preserve original quoting
    try:
        with open(CONFIG_FILE_PATH, "r") as f:
            current_config = json.load(f)
    except Exception:
        current_config = {}
    custom_map = current_config.get("FFMPEG_CUSTOM_PROFILES")
    if not isinstance(custom_map, dict):
        custom_map = {}

    enriched = []
    for p in profiles:
        name = p.get("name") or ""
        args = p.get("args") or []
        # Prefer the exact string from config for custom profiles; otherwise quote tokens
        if name in custom_map and isinstance(custom_map[name], str) and custom_map[name].strip():
            args_str = custom_map[name]
        else:
            try:
                args_str = " ".join(shlex.quote(str(t)) for t in args)
            except Exception:
                # Fallback to naive join if quoting fails
                args_str = " ".join(str(t) for t in args)
        enriched.append({
            "name": name,
            "args": args,
            "args_str": args_str
        })

    return {"profiles": enriched, "selected": selected}


@router.post("/api/ffmpeg/profiles/select", response_class=JSONResponse)
async def api_select_ffmpeg_profile(request: Request):
    try:
        payload = await request.json()
        name = (payload.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Missing profile name")
        # Validate and set selection in memory
        select_ffmpeg_profile(name)
        # Persist selection to config file
        try:
            try:
                with open(CONFIG_FILE_PATH, "r") as f:
                    current_config = json.load(f)
            except Exception:
                current_config = {}
            current_config["FFMPEG_PROFILE"] = name
            with open(CONFIG_FILE_PATH, "w") as f:
                json.dump(current_config, f, indent=4)
            # Update in-memory config
            config["FFMPEG_PROFILE"] = name
        except Exception as e:
            # Revert selection if persistence failed
            raise HTTPException(status_code=500, detail=f"Failed to persist selection: {e}")
        return {"success": True, "selected": name}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/ffmpeg/profiles", response_class=JSONResponse)
async def api_add_ffmpeg_profile(request: Request):
    try:
        payload = await request.json()

        name = (payload.get("name") or "").strip()
        raw_args = payload.get("args")
        if not name or raw_args in (None, ""):
            raise HTTPException(status_code=400, detail="Name and args are required")

        # Determine args list and args string for persistence
        if isinstance(raw_args, list):
            # Trust client-provided list of tokens
            try:
                args = [str(x) for x in raw_args]
            except Exception:
                raise HTTPException(status_code=400, detail="Args list must contain strings")
            args_str = " ".join(args)
        else:
            # Treat as a single shell-like string
            args_str = str(raw_args).strip()
            if not args_str:
                raise HTTPException(status_code=400, detail="Name and args are required")
            try:
                args = shlex.split(args_str)
            except ValueError as e:
                # shlex parsing error (e.g., unmatched quotes)
                raise HTTPException(status_code=400, detail=f"Invalid args format: {e}")

        # Heuristic: merge multi-token values for known single-value flags
        # This helps when users forget to quote values with spaces (e.g., -user_agent Foo Bar)
        SINGLE_VALUE_FLAGS = {"-user_agent"}
        merged_args = []
        i = 0
        while i < len(args):
            token = args[i]
            if token in SINGLE_VALUE_FLAGS:
                merged_args.append(token)
                i += 1
                # Collect subsequent tokens until the next flag-like token (starting with '-')
                value_parts = []
                while i < len(args) and not (isinstance(args[i], str) and args[i].startswith("-")):
                    value_parts.append(args[i])
                    i += 1
                if not value_parts:
                    # Missing value for single-value flag; keep behavior consistent with ffmpeg (will error later)
                    pass
                else:
                    merged_args.append(" ".join(value_parts))
                continue
            else:
                merged_args.append(token)
                i += 1
        args = merged_args

        # Validate that the required placeholder token is present as its own argument
        if "{input}" not in args:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Missing required {input} placeholder. Include {input} (e.g., -i {input})."
                ),
            )

        # Register in memory
        register_ffmpeg_profile(name, args)

        # Persist to config file under FFMPEG_CUSTOM_PROFILES
        try:
            try:
                with open(CONFIG_FILE_PATH, "r") as f:
                    current_config = json.load(f)
            except Exception:
                current_config = {}
            custom = current_config.get("FFMPEG_CUSTOM_PROFILES")
            if not isinstance(custom, dict):
                custom = {}
            custom[name] = args_str
            current_config["FFMPEG_CUSTOM_PROFILES"] = custom
            with open(CONFIG_FILE_PATH, "w") as f:
                json.dump(current_config, f, indent=4)
            # Update in-memory config
            config["FFMPEG_CUSTOM_PROFILES"] = custom
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to persist profile: {e}")
        return {"success": True, "name": name}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/api/ffmpeg/profiles/{name}", response_class=JSONResponse)
def api_delete_ffmpeg_profile(name: str):
    if not name:
        raise HTTPException(status_code=400, detail="Missing profile name")
    ok = delete_ffmpeg_profile(name)
    if not ok:
        raise HTTPException(status_code=400, detail="Cannot delete this profile or it does not exist")
    # Persist removal to config file and handle selection fallback
    try:
        try:
            with open(CONFIG_FILE_PATH, "r") as f:
                current_config = json.load(f)
        except Exception:
            current_config = {}
        custom = current_config.get("FFMPEG_CUSTOM_PROFILES")
        if isinstance(custom, dict) and name in custom:
            del custom[name]
            current_config["FFMPEG_CUSTOM_PROFILES"] = custom
        # If selected was this profile, fall back to CPU
        if current_config.get("FFMPEG_PROFILE") == name:
            current_config["FFMPEG_PROFILE"] = "CPU"
            config["FFMPEG_PROFILE"] = "CPU"
        with open(CONFIG_FILE_PATH, "w") as f:
            json.dump(current_config, f, indent=4)
        # Update in-memory config as well
        if isinstance(custom, dict):
            config["FFMPEG_CUSTOM_PROFILES"] = custom
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to persist deletion: {e}")
    return {"success": True, "name": name}

@router.put("/api/ffmpeg/profiles/{name}", response_class=JSONResponse)
async def api_update_ffmpeg_profile(name: str, request: Request):
    """
    Update an existing custom FFmpeg profile's args.
    - Protect built-in profiles (CPU, CUDA) from modification.
    - Accepts either a JSON string `args` or a JSON array of tokens.
    - Validates that `{input}` placeholder is present as its own token.
    - Uses shlex.split for string parsing (quote-aware).
    - Persists to CONFIG_FILE_PATH under FFMPEG_CUSTOM_PROFILES and updates in-memory state.
    """
    try:
        if not name:
            raise HTTPException(status_code=400, detail="Missing profile name")
        if name in ("CPU", "CUDA"):
            raise HTTPException(status_code=400, detail="Cannot edit built-in profile")

        payload = await request.json()
        raw_args = payload.get("args")
        if raw_args in (None, ""):
            raise HTTPException(status_code=400, detail="Args are required")

        # Parse args
        if isinstance(raw_args, list):
            try:
                args = [str(x) for x in raw_args]
            except Exception:
                raise HTTPException(status_code=400, detail="Args list must contain strings")
            args_str = " ".join(args)
        else:
            args_str = str(raw_args).strip()
            if not args_str:
                raise HTTPException(status_code=400, detail="Args are required")
            try:
                args = shlex.split(args_str)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=f"Invalid args format: {e}")

        # Validate placeholder
        if "{input}" not in args:
            raise HTTPException(status_code=400, detail="Missing required {input} placeholder. Include {input} (e.g., -i {input}).")

        # Register/overwrite in memory
        register_ffmpeg_profile(name, args)

        # Persist to config file
        try:
            try:
                with open(CONFIG_FILE_PATH, "r") as f:
                    current_config = json.load(f)
            except Exception:
                current_config = {}
            custom = current_config.get("FFMPEG_CUSTOM_PROFILES")
            if not isinstance(custom, dict):
                custom = {}
            custom[name] = args_str
            current_config["FFMPEG_CUSTOM_PROFILES"] = custom
            with open(CONFIG_FILE_PATH, "w") as f:
                json.dump(current_config, f, indent=4)
            # Update in-memory config
            config["FFMPEG_CUSTOM_PROFILES"] = custom
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to persist profile: {e}")

        return {"success": True, "name": name}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/api/streams/stop", response_class=JSONResponse)
async def api_stop_stream(request: Request):
    """
    Stop a running stream for the given channel.
    Expects JSON: { "channel": "<channel id or number>" }

    This attempts to call a stopping function from the streaming module:
    - stop_stream_by_channel(channel)
    - stop_stream(channel)

    The function may be synchronous or asynchronous.
    """
    try:
        payload = await request.json()
        channel = str((payload.get("channel") or "")).strip()
        if not channel:
            raise HTTPException(status_code=400, detail="Missing channel")

        # Defer import to runtime to avoid circular imports
        try:
            from . import streaming as streaming_module
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Streaming module unavailable: {e}")

        # Try known function names
        stop_fn = getattr(streaming_module, "stop_stream_by_channel", None)
        if stop_fn is None:
            stop_fn = getattr(streaming_module, "stop_stream", None)

        if stop_fn is None or not callable(stop_fn):
            # Backend does not yet expose a stop function
            return JSONResponse(status_code=501, content={
                "success": False,
                "message": "Stop operation not implemented on server. Please add stop_stream_by_channel(channel) or stop_stream(channel)."
            })

        # Support both sync and async implementations
        ok = None
        try:
            if asyncio.iscoroutinefunction(stop_fn):
                ok = await stop_fn(channel)
            else:
                ok = stop_fn(channel)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error while stopping stream: {e}")

        # Interpret return value; treat None as success if no exception
        if ok is False:
            return JSONResponse(status_code=404, content={
                "success": False,
                "message": "Stream not found or already stopped",
                "channel": channel
            })

        return {"success": True, "channel": channel}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
