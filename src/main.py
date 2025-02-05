from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from .routes import router as app_router
from .database import init_db
from .m3u import load_m3u_files

app = FastAPI()

# Mount the static directory so that files in /static/ are served.
app.mount("/static", StaticFiles(directory="static"), name="static")

# Include all routes.
app.include_router(app_router)

@app.on_event("startup")
def startup_event():
    init_db()         # Create and update the database schema
    load_m3u_files()  # Load the M3U files (this will also update the EPG)
