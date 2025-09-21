import asyncio
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import os
import subprocess

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

# --- GPU / CUDA detection helpers ---
def _run_cmd(cmd: list[str]) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            text=True,
            timeout=10,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except FileNotFoundError as e:
        return 127, "", str(e)
    except subprocess.TimeoutExpired:
        return 124, "", "Command timed out"


def detect_cuda_support() -> dict:
    """Detect whether NVIDIA GPU and CUDA hwaccel are available to this container.

    Returns a dict with keys:
      - gpu_available: bool
      - nvidia_smi_rc: int
      - nvidia_smi_output: str
      - ffmpeg_hwaccels_rc: int
      - ffmpeg_hwaccels: list[str]
      - cuda_in_hwaccels: bool
    """
    # Check nvidia-smi presence and output
    smi_rc, smi_out, smi_err = _run_cmd(["nvidia-smi"])  # requires `--gpus all` in Docker
    gpu_available = smi_rc == 0

    # Ask ffmpeg what hwaccels it supports
    ff_rc, ff_out, ff_err = _run_cmd(["ffmpeg", "-hide_banner", "-hwaccels"])  # requires ffmpeg compiled with CUDA
    # ffmpeg prints list of accelerators on stdout, one per line after a header
    hw_lines = []
    if ff_rc == 0 and ff_out:
        hw_lines = [ln.strip() for ln in ff_out.splitlines() if ln.strip() and not ln.lower().startswith("hardware acceleration methods")]
    cuda_in = any(ln.lower() == "cuda" for ln in hw_lines)

    return {
        "gpu_available": bool(gpu_available),
        "nvidia_smi_rc": smi_rc,
        "nvidia_smi_output": smi_out if smi_out else smi_err,
        "ffmpeg_hwaccels_rc": ff_rc,
        "ffmpeg_hwaccels": hw_lines,
        "cuda_in_hwaccels": bool(cuda_in),
    }

# Mount static directories
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/custom_logos", StaticFiles(directory=CUSTOM_LOGOS_DIR), name="custom_logos")
app.mount("/schedulesdirect_cache", StaticFiles(directory="config/schedulesdirect_cache"), name="schedulesdirect_cache")

# Include all routes
app.include_router(app_router)
app.include_router(settings_router)
app.include_router(status_router)

@app.get("/health/gpu")
def gpu_health():
    """Return information about NVIDIA/CUDA availability inside this container."""
    info = detect_cuda_support()
    # Also reflect what the app decided at startup
    decided = {
        "use_cuda": bool(config.get("USE_CUDA", False)),
        "ffmpeg_accel_args": config.get("FFMPEG_ACCEL_ARGS", ""),
        "ffmpeg_encoder": config.get("FFMPEG_ENCODER", ""),
    }
    return {"detection": info, "decision": decided}

@app.on_event("startup")
async def startup_event():
    # Detect NVIDIA/CUDA availability and configure ffmpeg usage
    force_disable = os.getenv("FORCE_DISABLE_CUDA", "").lower() in ("1", "true", "yes")
    info = detect_cuda_support() if not force_disable else {"gpu_available": False, "cuda_in_hwaccels": False}

    # Log detailed GPU detection info
    if force_disable:
        print("[Startup][GPU] CUDA check skipped: FORCE_DISABLE_CUDA is set.")
    else:
        smi_rc = info.get("nvidia_smi_rc")
        smi_out = info.get("nvidia_smi_output", "")
        if smi_rc == 0 and smi_out:
            smi_lines = [ln for ln in smi_out.splitlines() if ln.strip()]
            smi_summary = " | ".join(smi_lines[:2]) if smi_lines else "(no output)"
            print(f"[Startup][GPU] nvidia-smi OK (rc=0). Summary: {smi_summary}")
        else:
            print(f"[Startup][GPU] nvidia-smi not available or failed (rc={smi_rc}). Output: {smi_out}")

        ff_rc = info.get("ffmpeg_hwaccels_rc")
        hwaccels = info.get("ffmpeg_hwaccels", [])
        if ff_rc == 0:
            print(f"[Startup][GPU] FFmpeg hwaccels: {', '.join(hwaccels) if hwaccels else '(none)'}")
        else:
            print(f"[Startup][GPU] FFmpeg -hwaccels failed (rc={ff_rc}).")

    use_cuda = (not force_disable) and info.get("gpu_available") and info.get("cuda_in_hwaccels")

    if use_cuda:
        print("[Startup][GPU] Decision: USE_CUDA=True (GPU present and FFmpeg reports 'cuda' hwaccel)")
    else:
        reason = []
        if force_disable:
            reason.append("FORCE_DISABLE_CUDA=1")
        if not info.get("gpu_available"):
            reason.append("nvidia-smi unavailable")
        if not info.get("cuda_in_hwaccels"):
            reason.append("FFmpeg lacks 'cuda' hwaccel")
        print(f"[Startup][GPU] Decision: USE_CUDA=False ({', '.join(reason) if reason else 'unknown reason'})")

    # Persist decision into config for other modules to use when constructing ffmpeg commands
    config["USE_CUDA"] = bool(use_cuda)
    if use_cuda:
        # Typical CUDA acceleration flags; adjust as needed where you build commands
        # -hwaccel cuda enables decode acceleration (when supported)
        # -hwaccel_output_format cuda keeps frames on GPU
        # Use NVENC encoders when encoding
        config["FFMPEG_ACCEL_ARGS"] = "-hwaccel cuda -hwaccel_output_format cuda"
        # Choose a sensible default encoder; callers can override per codec
        config["FFMPEG_ENCODER"] = "h264_nvenc"
        print("[Startup] CUDA detected. Enabling FFmpeg CUDA acceleration and NVENC by default.")
    else:
        config["FFMPEG_ACCEL_ARGS"] = ""
        config["FFMPEG_ENCODER"] = ""
        if force_disable:
            print("[Startup] FORCE_DISABLE_CUDA is set. CUDA acceleration disabled.")
        else:
            print("[Startup] CUDA not available. Running with CPU-only FFmpeg.")

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
