import os
import sqlite3
import requests
import subprocess
from fastapi import FastAPI, Query
from fastapi.responses import StreamingResponse, JSONResponse, Response
from datetime import datetime, timedelta

# Define Config Directory
CONFIG_DIR = "config"
M3U_DIR = os.path.join(CONFIG_DIR, "m3u")
DB_FILE = os.path.join(CONFIG_DIR, "iptv_channels.db")

# Ensure Config Directory Exists
os.makedirs(CONFIG_DIR, exist_ok=True)
os.makedirs(M3U_DIR, exist_ok=True)

def find_m3u_files():
    return [os.path.join(M3U_DIR, file) for file in os.listdir(M3U_DIR) if file.endswith(".m3u")]

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

# Function to fetch & parse M3U playlists
def load_m3u_from_directory():
    m3u_files = find_m3u_files()
    if not m3u_files:
        print("[INFO] No M3U files found in the directory.")
        return "No M3U files found."
    
    print(f"[INFO] Found {len(m3u_files)} M3U files. Scanning...")
    
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM channels")  # Clear previous entries
    
    for m3u_file in m3u_files:
        print(f"[INFO] Processing M3U file: {m3u_file}")
        with open(m3u_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        
        for i in range(len(lines)):
            if lines[i].startswith("#EXTINF"):
                name = lines[i].split(",")[-1].strip()
                url = lines[i + 1].strip()
                c.execute("INSERT INTO channels (name, url) VALUES (?, ?)", (name, url))
    
    conn.commit()
    conn.close()
    print("[SUCCESS] M3U files loaded successfully!")
    return "M3U files loaded!"

# Load M3U on startup
load_m3u_from_directory()

# Initialize FastAPI
app = FastAPI()

# Get server IP from environment variable
import socket

def get_server_ip():
    try:
        hostname = socket.gethostname()
        return socket.gethostbyname(hostname)
    except Exception:
        return "127.0.0.1"

@app.get("/channels")
def list_channels():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, name FROM channels")
    data = c.fetchall()
    conn.close()
    return {"channels": data}

@app.get("/discover.json")
def discover():
    server_ip = get_server_ip()
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

@app.get("/lineup.json")
def lineup():
    server_ip = get_server_ip()
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

@app.get("/lineup_status.json")
def lineup_status():
    return JSONResponse(content={"ScanInProgress": 0, "ScanPossible": 1, "Source": "Cable", "SourceList": ["Cable"]})

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
    
    ffmpeg_cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-user_agent", "VLC/3.0.20-git LibVLC/3.0.20-git",
        "-re", "-i", url,
        "-max_muxing_queue_size", "1024",
        "-c:v", "copy", "-c:a", "ac3",
        "-bufsize", "5M",
        "-f", "mpegts", "pipe:1"
    ]

    try:
        process = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=10**8)
        return StreamingResponse(process.stdout, media_type="video/mp2t")
    except Exception as e:
        return {"error": f"Failed to start FFmpeg: {str(e)}"}
