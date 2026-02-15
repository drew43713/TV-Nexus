from fastapi import FastAPI, HTTPException
import asyncio
from fastapi.staticfiles import StaticFiles
import os
import subprocess
import logging

from .gpu import detect_cuda_support

from .logging_config import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

# Initialize config early (before importing modules that use config constants).
from . import config as config_module

config_module.init_config()

# Import modules/routes after config init so they see the correct constants.
from .routes import router as app_router
from .status import router as status_router
from .settings import router as settings_router
from .database import init_db
from .m3u import load_m3u_files

from .config import config, CUSTOM_LOGOS_DIR, USE_PREGENERATED_DATA
from .tasks import start_epg_reparse_task

app = FastAPI()


@app.get("/health/db")
def db_health():
    """Return sqlite PRAGMAs to confirm WAL/busy_timeout are active."""
    try:
        from .db import get_conn

        with get_conn() as conn:
            jm = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            bt = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
            sync = conn.execute("PRAGMA synchronous;").fetchone()[0]
        return {"journal_mode": jm, "busy_timeout_ms": bt, "synchronous": sync}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Mount static directories
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/custom_logos", StaticFiles(directory=CUSTOM_LOGOS_DIR), name="custom_logos")
app.mount(
    "/schedulesdirect_cache",
    StaticFiles(directory="config/schedulesdirect_cache"),
    name="schedulesdirect_cache",
)

# Include all routes
app.include_router(app_router)
app.include_router(settings_router)
app.include_router(status_router)


@app.get("/health/gpu")
def gpu_health():
    info = detect_cuda_support()
    selection = {
        "ffmpeg_profile": config.get("FFMPEG_PROFILE"),
        "ffmpeg_custom_args": config.get("FFMPEG_CUSTOM_ARGS"),
    }
    return {"detection": info, "selection": selection}


@app.on_event("startup")
async def startup_event():
    force_disable = os.getenv("FORCE_DISABLE_CUDA", "").lower() in ("1", "true", "yes")
    info = detect_cuda_support() if not force_disable else {"gpu_available": False, "cuda_in_hwaccels": False}

    if force_disable:
        logger.info("[Startup][GPU] CUDA check skipped: FORCE_DISABLE_CUDA is set.")
    else:
        smi_rc = info.get("nvidia_smi_rc")
        smi_out = info.get("nvidia_smi_output", "")
        if smi_rc == 0 and smi_out:
            smi_lines = [ln for ln in smi_out.splitlines() if ln.strip()]
            smi_summary = " | ".join(smi_lines[:2]) if smi_lines else "(no output)"
            logger.info("[Startup][GPU] nvidia-smi OK (rc=0). Summary: %s", smi_summary)
        else:
            logger.info("[Startup][GPU] nvidia-smi not available or failed (rc=%s). Output: %s", smi_rc, smi_out)

        ff_rc = info.get("ffmpeg_hwaccels_rc")
        hwaccels = info.get("ffmpeg_hwaccels", [])
        if ff_rc == 0:
            logger.info("[Startup][GPU] FFmpeg hwaccels: %s", ", ".join(hwaccels) if hwaccels else "(none)")
        else:
            logger.info("[Startup][GPU] FFmpeg -hwaccels failed (rc=%s).", ff_rc)

    logger.info("[Startup][GPU] Auto-selection disabled. Choose an ffmpeg profile in settings.")

    init_db()
    if not USE_PREGENERATED_DATA:
        load_m3u_files()
        if config["REPARSE_EPG_INTERVAL"] > 0:
            await start_epg_reparse_task()
    else:
        logger.info("[Startup] USE_PREGENERATED_DATA is True: skipping M3U load and EPG re-parse task.")
