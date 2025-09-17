import asyncio
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

# Import your existing modules/routes
from .routes import router as app_router
from .status import router as status_router
from .settings import router as settings_router
from .database import init_db
from .m3u import load_m3u_files

# Import 'config' and the new function for re-parse tasks
from .config import config, LOGOS_DIR, CUSTOM_LOGOS_DIR, USE_PREGENERATED_DATA
from .tasks import start_epg_reparse_task

app = FastAPI()

# Mount static directories
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/custom_logos", StaticFiles(directory=CUSTOM_LOGOS_DIR), name="custom_logos")
app.mount("/schedulesdirect_cache", StaticFiles(directory="config/schedulesdirect_cache"), name="schedulesdirect_cache")

# Include all routes
app.include_router(app_router)
app.include_router(settings_router)
app.include_router(status_router)

@app.on_event("startup")
async def startup_event():
    # Initialize the database and load M3U files on startup.
    init_db()
    if not USE_PREGENERATED_DATA:
        # Load M3U files and start EPG background task as usual
        load_m3u_files()
        if config["REPARSE_EPG_INTERVAL"] > 0:
            await start_epg_reparse_task()
    else:
        # Skipping M3U load and EPG re-parse as requested; using pre-generated data
        print("[Startup] USE_PREGENERATED_DATA is True: skipping M3U load and EPG re-parse task.")
