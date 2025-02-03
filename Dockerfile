# Start with an official Python base image
FROM python:3.10-slim

# Install FFmpeg and dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Set the working directory inside the container
WORKDIR /app

# Copy dependency list and install required Python packages
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the full application code
COPY . .

# Expose the application port
EXPOSE 8100

# Start the FastAPI application with Uvicorn
CMD ["uvicorn", "iptv_hdhr_poc:app", "--host", "0.0.0.0", "--port", "8100"]
