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
from .config import config, LOGOS_DIR, CUSTOM_LOGOS_DIR
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
    load_m3u_files()

    # If REPARSE_EPG_INTERVAL > 0, spawn the background re-parse immediately.
    if config["REPARSE_EPG_INTERVAL"] > 0:
        await start_epg_reparse_task()
