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

# Ensure Config Directories Exist
os.makedirs(M3U_DIR, exist_ok=True)
os.makedirs(CONFIG_DIR, exist_ok=True)

def find_m3u_file():
    for file in os.listdir(M3U_DIR):
        if file.endswith(".m3u"):
            return os.path.join(M3U_DIR, file)
    return None

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
def load_m3u_from_file():
    M3U_FILE = find_m3u_file()
    if not M3U_FILE:
        print("[INFO] No M3U file found in the directory.")
        return "No M3U file found."
    
    print(f"[INFO] Found M3U file: {M3U_FILE}. Scanning...")
    
    with open(M3U_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
    
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM channels")  # Clear previous entries
    
    for i in range(len(lines)):
        if lines[i].startswith("#EXTINF"):
            name = lines[i].split(",")[-1].strip()
            url = lines[i + 1].strip()
            c.execute("INSERT INTO channels (name, url) VALUES (?, ?)", (name, url))
    
    conn.commit()
    conn.close()
    print("[SUCCESS] M3U Loaded successfully!")
    return "M3U Loaded!"

# Load M3U on startup
load_m3u_from_file()

# Initialize FastAPI
app = FastAPI()

# Get server IP from environment variable
def get_server_ip():
    return os.getenv("SERVER_IP", "127.0.0.1")

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
