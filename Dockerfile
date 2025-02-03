# Stage 1: Get FFmpeg from linuxserver.io image
FROM lscr.io/linuxserver/ffmpeg:latest as ffmpeg

# Stage 2: Use a lightweight Python image
FROM python:3.10-slim

# Copy FFmpeg binaries from Stage 1
COPY --from=ffmpeg /usr/bin/ffmpeg /usr/bin/ffmpeg
COPY --from=ffmpeg /usr/bin/ffprobe /usr/bin/ffprobe

# Set working directory
WORKDIR /app

# Install dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose the application port
EXPOSE 8100

# Run the application
CMD ["uvicorn", "iptv_hdhr_poc:app", "--host", "0.0.0.0", "--port", "8100"]
