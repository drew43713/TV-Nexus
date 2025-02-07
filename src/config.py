import os

# -- Configuration and Directory Setup --
CONFIG_DIR = "config"
M3U_DIR = os.path.join(CONFIG_DIR, "m3u")
EPG_DIR = os.path.join(CONFIG_DIR, "epg")
DB_FILE = os.path.join(CONFIG_DIR, "iptv_channels.db")
MODIFIED_EPG_DIR = os.path.join(CONFIG_DIR, "epg_modified")
LOGOS_DIR = os.path.join("static", "logos")

os.makedirs(CONFIG_DIR, exist_ok=True)
os.makedirs(M3U_DIR, exist_ok=True)
os.makedirs(EPG_DIR, exist_ok=True)
os.makedirs(MODIFIED_EPG_DIR, exist_ok=True)
os.makedirs(LOGOS_DIR, exist_ok=True)