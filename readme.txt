version: '3.9'

services:
  iptv_server:
    build:
      context: https://github.com/drew43713/plex-iptv.git
    ports:
      - "8100:8100"
    volumes:
      - /appdata/plex-iptv:/app/config  # Persist the SQLite database
