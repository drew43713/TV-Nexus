# Use FFmpeg base image
FROM lscr.io/linuxserver/ffmpeg:latest

# Install Python and pip
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip && \
    rm -rf /var/lib/apt/lists/*

# Set the working directory
WORKDIR /app

# Copy and install Python dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose the application port
EXPOSE 8100

# Command to run the application
CMD ["uvicorn", "iptv_hdhr_poc:app", "--host", "0.0.0.0", "--port", "8100"]
