import sqlite3
import requests
import subprocess
from fastapi import FastAPI, Query
from fastapi.responses import StreamingResponse, JSONResponse, Response
from datetime import datetime, timedelta

# Initialize FastAPI app
app = FastAPI()

# Database setup
DB_FILE = "iptv_channels.db"
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS channels (
                    id INTEGER PRIMARY KEY, 
                    name TEXT, 
                    url TEXT)''')
    conn.commit()
    conn.close()
init_db()

# Function to fetch & parse M3U playlist
def load_m3u_from_url(m3u_url: str):
    response = requests.get(m3u_url)
    if response.status_code != 200:
        return "Failed to fetch M3U"
    
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    lines = response.text.splitlines()
    
    for i in range(len(lines)):
        if lines[i].startswith("#EXTINF"):
            name = lines[i].split(",")[-1]
            url = lines[i + 1].strip()
            c.execute("INSERT INTO channels (name, url) VALUES (?, ?)", (name, url))
    conn.commit()
    conn.close()
    return "M3U Loaded!"

# API endpoint to load M3U playlist
@app.get("/load_m3u")
def load_m3u(m3u_url: str = Query(...)):
    try:
        response = requests.get(m3u_url)
        if response.status_code != 200:
            return {"error": "Failed to fetch M3U file."}

        m3u_content = response.text
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()

        lines = m3u_content.splitlines()
        for i in range(len(lines)):
            if lines[i].startswith("#EXTINF"):
                name = lines[i].split(",")[-1].strip()
                url = lines[i + 1].strip()
                c.execute("INSERT INTO channels (name, url) VALUES (?, ?)", (name, url))

        conn.commit()
        conn.close()

        return {"message": "M3U Loaded Successfully"}
    except Exception as e:
        return {"error": str(e)}

# API endpoint to list available channels
@app.get("/channels")
def list_channels():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, name FROM channels")
    data = c.fetchall()
    conn.close()
    return {"channels": data}

# API endpoint to serve a stream via FFmpeg
@app.get("/tuner/{channel_id}")
def stream_channel(channel_id: int):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT url FROM channels WHERE id = ?", (channel_id,))
    result = c.fetchone()
    conn.close()
    
    if not result:
        return {"error": "Channel not found"}
    
    url = result[0]
    
    # Optimized FFmpeg command to reduce buffering
    ffmpeg_cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-user_agent", "Mozilla/5.0",  # Pretend to be a browser
        "-re", "-i", url,
        "-c:v", "copy", "-c:a", "ac3",
        "-f", "mpegts", "pipe:1"
    ]

    try:
        process = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=10**8)
        return StreamingResponse(process.stdout, media_type="video/mp2t")
    except Exception as e:
        return {"error": f"Failed to start FFmpeg: {str(e)}"}

# HDHomeRun required endpoints
@app.get("/discover.json")
def discover():
    server_ip = "10.0.0.92"  # Replace with your actual server IP
    return JSONResponse(content={
        "FriendlyName": "IPTV HDHomeRun",
        "Manufacturer": "Custom",
        "ModelNumber": "HDTC-2US",
        "FirmwareName": "hdhomeruntc_atsc",
        "FirmwareVersion": "20250802",
        "DeviceID": "12345678",
        "DeviceAuth": "testauth",
        "BaseURL": f"http://{server_ip}:8100",
        "LineupURL": f"http://{server_ip}:8100/lineup.json"
    })

@app.get("/lineup_status.json")
def lineup_status():
    return JSONResponse(content={"ScanInProgress": 0, "ScanPossible": 1, "Source": "Cable", "SourceList": ["Cable"]})

@app.get("/lineup.json")
def lineup():
    server_ip = "10.0.0.92"  # Replace with your actual server IP
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, name, url FROM channels")
    channels = c.fetchall()
    conn.close()

    lineup = []
    for channel_id, name, url in channels:
        lineup.append({
            "GuideNumber": str(channel_id),
            "GuideName": name,
            "URL": f"http://{server_ip}:8100/tuner/{channel_id}"
        })

    return JSONResponse(content=lineup)

# API endpoint to serve EPG (Electronic Program Guide)
@app.get("/epg")
def generate_epg():
    """Generate an XMLTV EPG for Plex."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, name FROM channels")
    channels = c.fetchall()
    conn.close()

    now = datetime.utcnow()
    epg = '<?xml version="1.0" encoding="UTF-8"?>\n'
    epg += '<tv generator-info-name="IPTV-Server">\n'

    for channel_id, channel_name in channels:
        sanitized_name = channel_name.replace("&", "&amp;")

        epg += f'  <channel id="{channel_id}">\n'
        epg += f'    <display-name>{sanitized_name}</display-name>\n'
        epg += '  </channel>\n'

        # Add sample program schedule for 24 hours
        for i in range(24):  # One program per hour
            start_time = now + timedelta(hours=i)
            end_time = start_time + timedelta(hours=1)
            epg += f'  <programme start="{start_time.strftime("%Y%m%d%H%M%S")} +0000" '
            epg += f'stop="{end_time.strftime("%Y%m%d%H%M%S")} +0000" channel="{channel_id}">\n'
            epg += f'    <title>Sample Show {i + 1}</title>\n'
            epg += f'    <desc>Sample description for show {i + 1} on {sanitized_name}.</desc>\n'
            epg += '  </programme>\n'

    epg += '</tv>\n'
    return Response(content=epg, media_type="application/xml")
