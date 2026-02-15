import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Path to the config file (inside the config directory)
CONFIG_FILE_PATH = os.path.join("config", "config.json")

# Default configuration values.
DEFAULT_CONFIG: dict[str, Any] = {
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
    # Optional variable:
    "DOMAIN_NAME": "",  # e.g. "mydomain.com"
    "EPG_COLORS_FILE": os.path.join("config", "epg", "epg_colors.json"),
    # How often to automatically re-parse EPG (in minutes); 0 = disabled
    "REPARSE_EPG_INTERVAL": 1440,  # 1440 = 24 hours
    "USE_PREGENERATED_DATA": False,
    "FFMPEG_PROFILE": "CPU",
    "FFMPEG_CUSTOM_PROFILES": {},
}


def load_config() -> dict[str, Any]:
    """Load config from defaults + config.json + environment overrides."""
    cfg = DEFAULT_CONFIG.copy()

    if os.path.exists(CONFIG_FILE_PATH):
        try:
            with open(CONFIG_FILE_PATH, "r") as f:
                file_config = json.load(f)
            if isinstance(file_config, dict):
                cfg.update(file_config)
        except Exception as e:
            logger.info("Error reading config file: %s", e)

    # Override with environment variables (if they exist).
    for key in list(cfg.keys()):
        env_value = os.environ.get(key)
        if env_value is None:
            continue

        if key in ["PORT", "TUNER_COUNT", "REPARSE_EPG_INTERVAL"]:
            try:
                cfg[key] = int(env_value)
            except ValueError:
                logger.info(
                    "Invalid %s value in environment: %s. Using %s instead.",
                    key,
                    env_value,
                    cfg[key],
                )
        elif key in ["USE_PREGENERATED_DATA"]:
            truthy = {"1", "true", "yes", "on"}
            falsy = {"0", "false", "no", "off"}
            val = str(env_value).strip().lower()
            if val in truthy:
                cfg[key] = True
            elif val in falsy:
                cfg[key] = False
            else:
                cfg[key] = bool(val)
        elif key == "FFMPEG_CUSTOM_PROFILES":
            try:
                parsed = json.loads(env_value)
                if isinstance(parsed, dict):
                    cfg[key] = parsed
                else:
                    logger.info("Invalid FFMPEG_CUSTOM_PROFILES in environment (not a dict). Ignoring.")
            except Exception as e:
                logger.info("Invalid FFMPEG_CUSTOM_PROFILES JSON: %s. Ignoring.", e)
        elif key == "FFMPEG_PROFILE":
            cfg[key] = str(env_value)
        else:
            cfg[key] = env_value

    # Normalize URL_SCHEME
    scheme = str(cfg.get("URL_SCHEME", "http")).strip().lower()
    if scheme not in ("http", "https"):
        logger.info("Invalid URL_SCHEME '%s' in config/environment. Falling back to 'http'.", scheme)
        scheme = "http"
    cfg["URL_SCHEME"] = scheme

    return cfg


def ensure_dirs(cfg: dict[str, Any]) -> None:
    os.makedirs("config", exist_ok=True)
    os.makedirs("config/schedulesdirect_cache", exist_ok=True)
    os.makedirs(os.path.join("config", "epg"), exist_ok=True)

    # Ensure necessary directories exist.
    os.makedirs(str(cfg["LOGOS_DIR"]), exist_ok=True)
    os.makedirs(str(cfg["CUSTOM_LOGOS_DIR"]), exist_ok=True)


def save_config(cfg: dict[str, Any]) -> None:
    try:
        with open(CONFIG_FILE_PATH, "w") as f:
            json.dump(cfg, f, indent=4)
    except Exception as e:
        logger.info("Error writing config file: %s", e)


def apply_config(cfg: dict[str, Any]) -> None:
    """Apply cfg to this module's globals for backwards compatibility.

    This keeps existing imports working:
      from .config import DB_FILE, HOST_IP, config, ...

    NOTE: Call init_config() early in app startup before importing modules that
    depend on these constants.
    """
    global config
    global HOST_IP, PORT, M3U_DIR, EPG_DIR, MODIFIED_EPG_DIR, DB_FILE, LOGOS_DIR, CUSTOM_LOGOS_DIR
    global TUNER_COUNT, DOMAIN_NAME, EPG_COLORS_FILE, REPARSE_EPG_INTERVAL, URL_SCHEME
    global USE_PREGENERATED_DATA, FFMPEG_PROFILE, FFMPEG_CUSTOM_PROFILES, BASE_URL

    config = cfg

    HOST_IP = cfg["HOST_IP"]
    PORT = cfg["PORT"]
    M3U_DIR = cfg["M3U_DIR"]
    EPG_DIR = cfg["EPG_DIR"]
    MODIFIED_EPG_DIR = cfg["MODIFIED_EPG_DIR"]
    DB_FILE = cfg["DB_FILE"]
    LOGOS_DIR = cfg["LOGOS_DIR"]
    CUSTOM_LOGOS_DIR = cfg["CUSTOM_LOGOS_DIR"]
    TUNER_COUNT = cfg["TUNER_COUNT"]
    DOMAIN_NAME = cfg["DOMAIN_NAME"]
    EPG_COLORS_FILE = cfg["EPG_COLORS_FILE"]
    REPARSE_EPG_INTERVAL = cfg["REPARSE_EPG_INTERVAL"]
    URL_SCHEME = cfg["URL_SCHEME"]
    USE_PREGENERATED_DATA = cfg["USE_PREGENERATED_DATA"]
    FFMPEG_PROFILE = cfg["FFMPEG_PROFILE"]
    FFMPEG_CUSTOM_PROFILES = cfg["FFMPEG_CUSTOM_PROFILES"]

    if DOMAIN_NAME:
        BASE_URL = f"{URL_SCHEME}://{DOMAIN_NAME}"
    else:
        BASE_URL = f"{URL_SCHEME}://{HOST_IP}:{PORT}"


# Backwards-compatible module globals (populated by init_config)
config: dict[str, Any] = DEFAULT_CONFIG.copy()
HOST_IP: str = DEFAULT_CONFIG["HOST_IP"]
PORT: int = DEFAULT_CONFIG["PORT"]
M3U_DIR: str = DEFAULT_CONFIG["M3U_DIR"]
EPG_DIR: str = DEFAULT_CONFIG["EPG_DIR"]
MODIFIED_EPG_DIR: str = DEFAULT_CONFIG["MODIFIED_EPG_DIR"]
DB_FILE: str = DEFAULT_CONFIG["DB_FILE"]
LOGOS_DIR: str = DEFAULT_CONFIG["LOGOS_DIR"]
CUSTOM_LOGOS_DIR: str = DEFAULT_CONFIG["CUSTOM_LOGOS_DIR"]
TUNER_COUNT: int = DEFAULT_CONFIG["TUNER_COUNT"]
DOMAIN_NAME: str = DEFAULT_CONFIG["DOMAIN_NAME"]
EPG_COLORS_FILE: str = DEFAULT_CONFIG["EPG_COLORS_FILE"]
REPARSE_EPG_INTERVAL: int = DEFAULT_CONFIG["REPARSE_EPG_INTERVAL"]
URL_SCHEME: str = DEFAULT_CONFIG["URL_SCHEME"]
USE_PREGENERATED_DATA: bool = DEFAULT_CONFIG["USE_PREGENERATED_DATA"]
FFMPEG_PROFILE: str = DEFAULT_CONFIG["FFMPEG_PROFILE"]
FFMPEG_CUSTOM_PROFILES: dict[str, str] = DEFAULT_CONFIG["FFMPEG_CUSTOM_PROFILES"]
BASE_URL: str = f"{URL_SCHEME}://{HOST_IP}:{PORT}"


def init_config() -> dict[str, Any]:
    """Load config and apply it to module globals.

    We keep the old behavior of writing the merged config back to disk, but do
    it explicitly (not at import time).
    """
    cfg = load_config()
    ensure_dirs(cfg)
    save_config(cfg)
    apply_config(cfg)
    return cfg
