from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from .routes import router as app_router
from .status import router as status_router
from .settings import router as settings_router
from .database import init_db, start_db_worker, stop_worker
from .m3u import load_m3u_files
from .config import LOGOS_DIR, CUSTOM_LOGOS_DIR

app = FastAPI()

# Mount the static directory so that files in /static/ are served.
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/custom_logos", StaticFiles(directory=CUSTOM_LOGOS_DIR), name="custom_logos")
app.mount("/schedulesdirect_cache", StaticFiles(directory="config/schedulesdirect_cache"), name="schedulesdirect_cache")


# Include all routes.
app.include_router(app_router)
app.include_router(settings_router)
app.include_router(status_router)

# Initialize the database and load M3U files on startup.
@app.on_event("startup")
def startup_event():
    init_db()         # Create and update the database schema
    load_m3u_files()  # Load the M3U files (this will also update the EPG)
    global db_thread
    db_thread = start_db_worker()
    
@app.on_event("shutdown")
def on_shutdown():
    # Signal the worker to stop and wait for it to exit gracefully
    stop_worker.set()
    db_thread.join(timeout=2)