# Plex IPTV Server

This project provides an IPTV server implementation using **FastAPI**, allowing users to manage IPTV playlists (M3U files) and Electronic Program Guide (EPG) data, and stream channels with compatibility for Plex or other IPTV clients.

## Features
- **IPTV Playlist Management:** Supports parsing and managing M3U playlists.
- **EPG Integration:** Parses XMLTV-based EPG files for rich guide data.
- **Stream Remuxing:** Utilizes FFmpeg for streaming live channels to compatible clients.
- **Plex Integration:** Fully compatible with Plex Media Server for custom IPTV integration.

---

## Getting Started

### Prerequisites
Make sure you have the following installed:
- **Docker**: [Install Docker](https://docs.docker.com/get-docker/)
- **Docker Compose**: [Install Docker Compose](https://docs.docker.com/compose/install/)

### Installation
1. Set up the Docker Compose file:

```yaml
version: '3.9'

services:
  iptv_server:
    build:
      context: https://github.com/drew43713/plex-iptv.git
    ports:
      - "8100:8100"
    volumes:
      - /appdata/plex-iptv:/app/config  # Adjust the path based on your setup for persistent storage
```

2. Start the server:
   ```bash
   docker-compose up -d
   ```

> **Note**: Update the `volumes` path (`/appdata/plex-iptv:/app/config`) to fit your specific setup. This path determines where the SQLite database and configuration files are stored.

---

## Usage

### Accessing the IPTV Server
The IPTV server runs on port `8100`. Open your browser and navigate to:
```
http://<your-server-ip>:8100/web
```

### Integrating with Plex
1. Open Plex Media Server.
2. Add a new DVR and select the `Custom URL` option.
3. Use the following endpoints:
   - **Lineup URL:** `http://<your-server-ip>:8100/lineup.json`
   - **EPG URL:** `http://<your-server-ip>:8100/epg.xml`

### Customization
- **M3U Playlist Directory:** Place your `.m3u` files in `/appdata/plex-iptv/m3u`.
- **EPG Directory:** Place your `.xml` or `.xmltv` files in `/appdata/plex-iptv/epg`.

---

## Troubleshooting

### FFmpeg Stream Issues
If streams fail, ensure:
- The input M3U URLs are valid and accessible.
- FFmpeg is installed and properly configured.

### Plex Integration Issues
If Plex fails to detect the server:
- Ensure the `lineup.json` and `epg.xml` endpoints are accessible.
- Check Docker logs for errors:
  ```bash
  docker logs <container_name>
  ```

---

## Contributing
Contributions are welcome! Feel free to fork this repository and submit pull requests.

---

## License
This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

