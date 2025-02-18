import asyncio

from .config import config
from .epg import parse_raw_epg_files, build_combined_epg

async def schedule_epg_reparse():
    """
    Periodically re-parse the raw EPG and rebuild the combined EPG.
    Reads the interval from config["REPARSE_EPG_INTERVAL"] each loop.
    """
    while True:
        interval = config["REPARSE_EPG_INTERVAL"]

        # If interval <= 0, "disable" auto re-parsing. Sleep a short time and loop again.
        if interval <= 0:
            await asyncio.sleep(60)
            continue

        # Otherwise, sleep for 'interval' minutes
        await asyncio.sleep(interval * 60)

        try:
            parse_raw_epg_files()
            build_combined_epg()
            print("[INFO] Automatic EPG re-parse completed.")
        except Exception as e:
            print(f"[ERROR] Automatic EPG re-parse failed: {e}")

async def start_epg_reparse_task():
    """
    Cancel any existing EPG re-parse task and start a new one immediately.
    That way changes to REPARSE_EPG_INTERVAL can take effect at once.
    """
    old_task = config.get("epg_reparse_task")

    if old_task and not old_task.done():
        # Cancel the old task if it's still running
        old_task.cancel()
        print("[INFO] Old EPG re-parse task was canceled.")

    # Create a new background task
    new_task = asyncio.create_task(schedule_epg_reparse())
    config["epg_reparse_task"] = new_task
    print("[INFO] New EPG re-parse task started.")
