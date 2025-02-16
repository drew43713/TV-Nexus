# TV Nexus - IPTV Server

TV Nexus is an IPTV server implementation using FastAPI, enabling users to manage IPTV playlists (M3U files) and Electronic Program Guide (EPG) data while providing stream remuxing for compatibility with Plex and other IPTV clients.

## Features

- **IPTV Playlist Management**: Supports parsing and managing M3U playlists.
- **EPG Integration**: Parses XMLTV-based EPG files for rich guide data.
- **Stream Remuxing**: Utilizes FFmpeg to remux live streams for compatibility with Plex and other IPTV clients.
- **Plex Integration**: Fully compatible with Plex Media Server for custom IPTV integration.
- **Centralized Configuration**: All key settings (e.g., host IP, port, directory paths, database file, tuner count) are managed via a configuration file with environment variable overrides.
- **Channel Activation System**: Newly added channels are inactive by default. Use the web interface to activate or deactivate channels; only activated channels appear in the lineup and EPG.
- **Instant Channel Activation**: Toggle channel status instantly from the web interface.
- **EPG Parsing Control**: Re-parse EPG files on-demand via the settings page.
- **Real-Time Stream Status**: Displays up-to-date stream status information including current program, subscriber count, stream URL, video/audio codec details, and resolution.

## Getting Started

### Prerequisites

Ensure you have the following installed:

- **Docker**: [Install Docker](https://docs.docker.com/get-docker/)
- **Docker Compose**: [Install Docker Compose](https://docs.docker.com/compose/install/)

### Installation

#### 1. Set Up the Docker Compose File

Create a `docker-compose.yml` file similar to the example below:

```yaml
version: '3.9'

services:
  tv_nexus:
    build:
      context: https://github.com/drew43713/tv-nexus.git
    ports:
      - "8100:8100"
    environment:
      - HOST_IP=your.host.ip
      - PORT=8100  # default port
    volumes:
      - /appdata/tv-nexus:/app/config  # Adjust the path for persistent storage
```

#### 2. Start the Server

```sh
docker-compose up -d
```

> **Note:**  
> - Update the volumes path (`/appdata/tv-nexus:/app/config`) to suit your setup.  
> - You must provide your host IP via the `HOST_IP` environment variable or configuration file so that the app binds correctly.

## Configuration

TV Nexus uses a configuration file (`config/config.json`) to manage key settings:
- **HOST_IP**: The server’s host IP address.
- **PORT**: The port on which the server runs.
- **M3U_DIR**: Directory for M3U playlist files.
- **EPG_DIR**: Directory for raw EPG files.
- **MODIFIED_EPG_DIR**: Directory for the processed/combined EPG file.
- **DB_FILE**: Path to the SQLite database file.
- **LOGOS_DIR**: Directory for locally cached channel logos.
- **TUNER_COUNT**: Number of tuners available.

If the configuration file does not exist, TV Nexus creates one with default values. Environment variables override the configuration file values.

## Usage

### Accessing the IPTV Server

By default, the IPTV server runs on port `8100`. Open your browser and navigate to:

```
http://<your-server-ip>:8100/
```

### Integrating with Plex

Follow these steps to integrate TV Nexus with Plex DVR:

1. **Open Plex Media Server Settings:**  
   In your Plex web app, navigate to **Settings > Live TV & DVR**.

2. **Set Up Plex DVR:**  
   Click on **Set Up Plex DVR**. When prompted, choose to use a custom tuner.
> **Note:**  
> - If you are prompted to enter your postal code, you can choose to manually specify an epg file by clicking "Have an XMLTV guide on your server? Click here to use that instead." The server URL can be found below.

3. **Enter Your TV Nexus Endpoints:**  
   - **Default URL:**  
     ```
     http://<your-server-ip>:8100
     ```
   - **EPG URL:**  
     ```
     http://<your-server-ip>:8100/epg.xml
     ```  
   Replace `<your-server-ip>` with your server's actual IP address or domain.

4. **Follow On-Screen Instructions:**  
   Plex will scan the lineup and guide you through channel mapping and DVR setup.  
   For further details, see the [Plex Live TV & DVR Support Article](https://support.plex.tv/articles/225877347-live-tv-dvr/).

### Managing Channels and EPG Data

- **Channel Activation:**  
  Channels are added as inactive by default. Use the web interface to activate channels; only active channels appear in the lineup and EPG.

- **Stream Status:**  
  The real-time stream status section displays the current program, subscriber count, stream URL, and video/audio details for each channel. Look for this on the settings page.

## Troubleshooting

### Database Locked Errors

If you encounter "database locked" errors (commonly due to concurrent writes in SQLite), note that stream status updates pause during EPG parsing. If issues persist, consider:
- Reducing simultaneous write operations.
- Switching to a more robust database solution.

### FFmpeg Stream Issues

Ensure:
- The URLs in your M3U playlists are valid and accessible.
- FFmpeg is installed and configured properly.

### Plex Integration Issues

If Plex fails to detect your tuner:
- Verify that the `/lineup.json` and `/epg.xml` endpoints are accessible.
- Check Docker logs with:
  ```sh
  docker logs <container_name>
  ```

## Contributing

Contributions are welcome! Please fork this repository and submit pull requests with your improvements.

## License

This project is licensed under the **MIT License**. See the [LICENSE](LICENSE) file for details.