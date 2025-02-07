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
    "LOGOS_DIR": os.path.join("static", "logos")
}

# Ensure the config directory exists.
os.makedirs("config", exist_ok=True)

# Start with the default config.
config = DEFAULT_CONFIG.copy()

# If the config file exists, load its values.
if os.path.exists(CONFIG_FILE_PATH):
    try:
        with open(CONFIG_FILE_PATH, "r") as f:
            file_config = json.load(f)
        # Update our config with values from the file.
        config.update(file_config)
    except Exception as e:
        print(f"Error reading config file: {e}")

# Override with environment variables (if they exist).
for key in config.keys():
    env_value = os.environ.get(key)
    if env_value is not None:
        # For the PORT, ensure we store an integer.
        if key == "PORT":
            try:
                config[key] = int(env_value)
            except ValueError:
                print(f"Invalid PORT value in environment: {env_value}. Using {config[key]} instead.")
        else:
            config[key] = env_value

# Write the final configuration back to the config file.
try:
    with open(CONFIG_FILE_PATH, "w") as f:
        json.dump(config, f, indent=4)
except Exception as e:
    print(f"Error writing config file: {e}")

# Expose configuration values as module-level constants.
HOST_IP = config["HOST_IP"]
PORT = config["PORT"]
M3U_DIR = config["M3U_DIR"]
EPG_DIR = config["EPG_DIR"]
MODIFIED_EPG_DIR = config["MODIFIED_EPG_DIR"]
DB_FILE = config["DB_FILE"]
LOGOS_DIR = config["LOGOS_DIR"]
