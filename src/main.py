import asyncio
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .routes import router as app_router
from .status import router as status_router
from .settings import router as settings_router
from .database import init_db
from .m3u import load_m3u_files
from .epg import parse_raw_epg_files, build_combined_epg
from .config import config, LOGOS_DIR, CUSTOM_LOGOS_DIR

app = FastAPI()

# Mount static directories
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/custom_logos", StaticFiles(directory=CUSTOM_LOGOS_DIR), name="custom_logos")
app.mount("/schedulesdirect_cache", StaticFiles(directory="config/schedulesdirect_cache"), name="schedulesdirect_cache")

# Include routers
app.include_router(app_router)
app.include_router(settings_router)
app.include_router(status_router)


async def schedule_epg_reparse():
    """
    Periodically re-parse the raw EPG and rebuild the combined EPG.
    Reads the interval from the in-memory config each loop so changes
    can take effect without restarting.
    """
    while True:
        # Pull the current interval from your config dict.
        current_interval = config["REPARSE_EPG_INTERVAL"]
        
        # If disabled (0 or negative), just wait a short while and check again.
        if current_interval <= 0:
            await asyncio.sleep(60)  # Check again in 1 minute
            continue

        # Otherwise, sleep for the current interval in minutes:
        await asyncio.sleep(current_interval * 60)

        # Then parse EPG files:
        try:
            parse_raw_epg_files()
            build_combined_epg()
            print("[INFO] Automatic EPG re-parse completed.")
        except Exception as e:
            print(f"[ERROR] Automatic EPG re-parse failed: {e}")


@app.on_event("startup")
async def startup_event():
    # Initialize database, load M3U
    init_db()
    load_m3u_files()

    # If REPARSE_EPG_INTERVAL is > 0 at startup, spawn background task
    if config["REPARSE_EPG_INTERVAL"] > 0:
        asyncio.create_task(schedule_epg_reparse())
