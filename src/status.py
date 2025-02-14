import subprocess
import json
import sqlite3
import datetime
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from .streaming import shared_streams, streams_lock
from .config import DB_FILE

router = APIRouter()

@router.get("/api/stream_status")
def stream_status():
    """
    Returns status for each active stream, including:
      - channel_name: from the channels table (using channel_number)
      - subscriber_count: number of client subscribers
      - stream_url: input stream URL from the FFmpeg command
      - probe_info: technical info from ffprobe (codec, resolution, etc.)
      - current_program: the current program on air for this channel (if any)
    Only streams with is_running True are included.
    """
    status = {}
    
    # Build a dictionary mapping channel numbers (as strings) to channel names.
    channel_names = {}
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        # Now query channel_number instead of id.
        cursor.execute("SELECT channel_number, name FROM channels")
        for row in cursor.fetchall():
            # Use channel_number (converted to string) as key.
            channel_names[str(row[0])] = row[1]
        conn.close()
    except Exception as e:
        print("Error loading channel names:", e)
    
    with streams_lock:
        for channel_number, shared in shared_streams.items():
            if not shared.is_running:
                continue
            
            subscriber_count = len(shared.subscribers)
            # Extract stream URL from the FFmpeg command.
            stream_url = "Unknown"
            try:
                i_index = shared.ffmpeg_cmd.index("-i")
                stream_url = shared.ffmpeg_cmd[i_index + 1]
            except Exception as e:
                stream_url = f"Error extracting URL: {e}"
            
            # Run ffprobe on the stream URL.
            probe_info = {}
            try:
                cmd = [
                    "ffprobe",
                    "-v", "quiet",
                    "-print_format", "json",
                    "-show_format",
                    "-show_streams",
                    stream_url
                ]
                result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
                output = result.stdout.decode("utf-8")
                probe_info = json.loads(output)
            except Exception as e:
                probe_info = {"error": str(e)}
            
            # Lookup channel name using the channel_number (as string).
            channel_name = channel_names.get(str(channel_number), "N/A")
            
            # Query the current program for this channel using channel_number.
            current_program = None
            try:
                now = datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S") + " +0000"
                conn = sqlite3.connect(DB_FILE)
                cursor = conn.cursor()
                # Use channel_number (as string) in the lookup; your build_combined_epg() stores this in channel_tvg_name.
                cursor.execute("""
                    SELECT title, start, stop 
                    FROM epg_programs 
                    WHERE channel_tvg_name = ? AND start <= ? AND stop > ?
                    ORDER BY start DESC 
                    LIMIT 1
                """, (str(channel_number), now, now))
                row = cursor.fetchone()
                if row:
                    current_program = {"title": row[0], "start": row[1], "stop": row[2]}
                conn.close()
            except Exception as e:
                current_program = {"error": str(e)}
            
            status[str(channel_number)] = {
                "channel_name": channel_name,
                "subscriber_count": subscriber_count,
                "stream_url": stream_url,
                "probe_info": probe_info,
                "current_program": current_program
            }
    return JSONResponse(status)