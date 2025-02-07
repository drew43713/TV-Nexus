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
    environment:
      - HOST_IP=your.host.ip
      - PORT=8100 #default port
    volumes:
      - /appdata/plex-iptv:/app/config  # Adjust the path based on your setup for persistent storage
```

2. Start the server:
   ```bash
   docker-compose up -d
   ```

> **Note**: Update the `volumes` path (`/appdata/plex-iptv:/app/config`) to fit your specific setup. This path determines where the SQLite database and configuration files are stored.
> **Note**: You must provide your host ip address in the environment variable otherwise the app will choose the docker ip instead of the lan ip.
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
If streams fails, ensure:
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

Below is the updated README in raw text:

---
# Plex IPTV Server

This project provides an IPTV server implementation using **FastAPI**, allowing users to manage IPTV playlists (M3U files) and Electronic Program Guide (EPG) data, and stream channels with compatibility for Plex or other IPTV clients.

## Features
- **IPTV Playlist Management:** Supports parsing and managing M3U playlists.
- **EPG Integration:** Parses XMLTV-based EPG files for rich guide data.
- **Stream Remuxing:** Utilizes FFmpeg for streaming live channels to compatible clients.
- **Plex Integration:** Fully compatible with Plex Media Server for custom IPTV integration.
- **Centralized Configuration:** All key settings (e.g. host IP, port, directory paths, database file) are managed via a configuration file, with environment variable overrides.

---

## Getting Started

### Prerequisites
Make sure you have the following installed:
- **Docker**: [Install Docker](https://docs.docker.com/get-docker/)
- **Docker Compose**: [Install Docker Compose](https://docs.docker.com/compose/install/)

### Installation
1. **Set Up the Docker Compose File:**

   Create a `docker-compose.yml` file similar to the example below:

   ```yaml
   version: '3.9'

   services:
     iptv_server:
       build:
         context: https://github.com/drew43713/plex-iptv.git
       ports:
         - "8100:8100"
       environment:
         - HOST_IP=your.host.ip
         - PORT=8100 # default port
       volumes:
         - /appdata/plex-iptv:/app/config  # Adjust the path for persistent storage
   ```

2. **Start the Server:**
   ```bash
   docker-compose up -d
   ```

> **Note:**  
> - Update the `volumes` path (`/appdata/plex-iptv:/app/config`) to suit your setup. This path stores the SQLite database and configuration files.  
> - You **must** provide your host IP address via the `HOST_IP` environment variable; otherwise, the app may choose the Docker IP instead of your LAN IP.

---

## Configuration

The application uses a configuration file (`config/config.json`) to manage key settings. The configuration file includes the following elements:

- **HOST_IP:** The server's host IP address.
- **PORT:** The port on which the server will run.
- **M3U_DIR:** Directory for M3U playlist files.
- **EPG_DIR:** Directory for raw EPG files.
- **MODIFIED_EPG_DIR:** Directory for the processed/combined EPG file.
- **DB_FILE:** Path to the SQLite database file.
- **LOGOS_DIR:** Directory for locally cached channel logos.

If the configuration file does not exist, the application creates one with default values. Additionally, any corresponding environment variables will override the values in the configuration file. This centralized approach makes it simple to manage settings across the project.

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
- **M3U Playlist Directory:** Place your `.m3u` files in the directory specified by `M3U_DIR` (default: `/appdata/plex-iptv/m3u`).
- **EPG Directory:** Place your `.xml` or `.xmltv` files in the directory specified by `EPG_DIR` (default: `/appdata/plex-iptv/epg`).

---

## Troubleshooting

### FFmpeg Stream Issues
If streams fail, ensure that:
- The input M3U URLs are valid and accessible.
- FFmpeg is installed and properly configured.

### Plex Integration Issues
If Plex fails to detect the server:
- Verify that the `lineup.json` and `epg.xml` endpoints are accessible.
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
---