import os
import json

# Path to the config file (inside the config directory)
CONFIG_FILE_PATH = os.path.join("config", "config.json")

# Default configuration values.
DEFAULT_CONFIG = {
    "HOST_IP": "127.0.0.1",
    "PORT": 8100,
    "M3U_DIR": os.path.join("config", "m3u"),
    "EPG_DIR": os.path.join("config", "epg"),
    "MODIFIED_EPG_DIR": os.path.join("config", "epg_modified"),
    "DB_FILE": os.path.join("config", "iptv_channels.db"),
    "LOGOS_DIR": os.path.join("static", "logos"),
    "CUSTOM_LOGOS_DIR": os.path.join("config", "custom_logos"),
    "TUNER_COUNT": 1,
    # Protocol/scheme for generated URLs (e.g., lineup.json, epg.xml). Allowed: "http" or "https".
    "URL_SCHEME": "http",
    # New optional variable:
    "DOMAIN_NAME": "",   # e.g. "mydomain.com"
    "EPG_COLORS_FILE": os.path.join("config", "epg", "epg_colors.json"),
    # New: how often to automatically re-parse EPG (in minutes); 0 = disabled
    "REPARSE_EPG_INTERVAL": 1440,  # 1440 = 24 hours
    "USE_PREGENERATED_DATA": False,
    "FFMPEG_PROFILE": "CPU",
    "FFMPEG_CUSTOM_PROFILES": {},
}

# Ensure the config directory exists.
os.makedirs("config", exist_ok=True)
os.makedirs("config/schedulesdirect_cache", exist_ok=True)
os.makedirs(os.path.join("config", "epg"), exist_ok=True)  # Ensure the epg directory exists

# Start with the default config.
config = DEFAULT_CONFIG.copy()

# If the config file exists, load its values.
if os.path.exists(CONFIG_FILE_PATH):
    try:
        with open(CONFIG_FILE_PATH, "r") as f:
            file_config = json.load(f)
        config.update(file_config)
    except Exception as e:
        print(f"Error reading config file: {e}")

# Override with environment variables (if they exist).
for key in config.keys():
    env_value = os.environ.get(key)
    if env_value is not None:
        # For numeric values like PORT, TUNER_COUNT, and REPARSE_EPG_INTERVAL, store as int.
        if key in ["PORT", "TUNER_COUNT", "REPARSE_EPG_INTERVAL"]:
            try:
                config[key] = int(env_value)
            except ValueError:
                print(f"Invalid {key} value in environment: {env_value}. Using {config[key]} instead.")
        elif key in ["USE_PREGENERATED_DATA"]:
            # Accept typical truthy/falsey strings
            truthy = {"1", "true", "yes", "on"}
            falsy = {"0", "false", "no", "off"}
            val = str(env_value).strip().lower()
            if val in truthy:
                config[key] = True
            elif val in falsy:
                config[key] = False
            else:
                # Fallback: any non-empty string is treated as True
                config[key] = bool(val)
        elif key == "FFMPEG_CUSTOM_PROFILES":
            # Accept JSON string for custom profiles (name -> arg string)
            try:
                parsed = json.loads(env_value)
                if isinstance(parsed, dict):
                    config[key] = parsed
                else:
                    print(f"Invalid FFMPEG_CUSTOM_PROFILES in environment (not a dict). Ignoring.")
            except Exception as e:
                print(f"Invalid FFMPEG_CUSTOM_PROFILES JSON: {e}. Ignoring.")
        elif key == "FFMPEG_PROFILE":
            # Accept a simple string name for the selected profile
            config[key] = str(env_value)
        else:
            config[key] = env_value

# Normalize and validate URL_SCHEME
scheme = str(config.get("URL_SCHEME", "http")).strip().lower()
if scheme not in ("http", "https"):
    print(f"Invalid URL_SCHEME '{scheme}' in config/environment. Falling back to 'http'.")
    scheme = "http"
config["URL_SCHEME"] = scheme

# Write the final configuration back to the config file.
try:
    with open(CONFIG_FILE_PATH, "w") as f:
        json.dump(config, f, indent=4)
except Exception as e:
    print(f"Error writing config file: {e}")

# Ensure necessary directories exist.
os.makedirs(config["LOGOS_DIR"], exist_ok=True)
os.makedirs(config["CUSTOM_LOGOS_DIR"], exist_ok=True)

# Expose configuration values as module-level constants.
HOST_IP = config["HOST_IP"]
PORT = config["PORT"]
M3U_DIR = config["M3U_DIR"]
EPG_DIR = config["EPG_DIR"]
MODIFIED_EPG_DIR = config["MODIFIED_EPG_DIR"]
DB_FILE = config["DB_FILE"]
LOGOS_DIR = config["LOGOS_DIR"]
CUSTOM_LOGOS_DIR = config["CUSTOM_LOGOS_DIR"]
TUNER_COUNT = config["TUNER_COUNT"]
DOMAIN_NAME = config["DOMAIN_NAME"]
EPG_COLORS_FILE = config["EPG_COLORS_FILE"]
REPARSE_EPG_INTERVAL = config["REPARSE_EPG_INTERVAL"]  # In minutes
URL_SCHEME = config["URL_SCHEME"]
USE_PREGENERATED_DATA = config["USE_PREGENERATED_DATA"]
FFMPEG_PROFILE = config["FFMPEG_PROFILE"]
FFMPEG_CUSTOM_PROFILES = config["FFMPEG_CUSTOM_PROFILES"]

# Build the BASE_URL once using the selected URL_SCHEME:
# If DOMAIN_NAME is set (non-empty), use {URL_SCHEME}://{DOMAIN_NAME}
# Otherwise use {URL_SCHEME}://{HOST_IP}:{PORT}
if DOMAIN_NAME:
    BASE_URL = f"{URL_SCHEME}://{DOMAIN_NAME}"
else:
    BASE_URL = f"{URL_SCHEME}://{HOST_IP}:{PORT}"
