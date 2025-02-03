# Use a lightweight Python image with FFmpeg pre-installed
FROM jrottenberg/ffmpeg:4.4-python3.10-slim

# Set the working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY . .

# Expose the application port
EXPOSE 8100

# Command to run the application
CMD ["uvicorn", "iptv_hdhr_poc:app", "--host", "0.0.0.0", "--port", "8100"]
