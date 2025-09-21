import subprocess
import threading
import queue
from .config import config  # access runtime ffmpeg/gpu decisions
import sqlite3

streams_lock = threading.Lock()
shared_streams = {}

class SharedStream:
    def __init__(self, channel_id, ffmpeg_cmd):
        self.channel_id = channel_id
        self.ffmpeg_cmd = ffmpeg_cmd
        self.process = subprocess.Popen(
            ffmpeg_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=10**8
        )
        self.subscribers = []
        self.lock = threading.Lock()
        self.is_running = True
        self.broadcast_thread = threading.Thread(target=self._broadcast)
        self.broadcast_thread.daemon = True
        self.broadcast_thread.start()

    def _broadcast(self):
        while True:
            chunk = self.process.stdout.read(1024)
            if not chunk:
                print(f"Stream for channel {self.channel_id} ended.")
                break
            with self.lock:
                for q in self.subscribers:
                    q.put(chunk)
        self.is_running = False
        with self.lock:
            for q in self.subscribers:
                q.put(None)
        stderr_output = self.process.stderr.read()
        if stderr_output:
            print("FFmpeg stderr:", stderr_output.decode("utf-8", errors="ignore"))

    def add_subscriber(self):
        q = queue.Queue()
        with self.lock:
            self.subscribers.append(q)
        return q

    def remove_subscriber(self, q):
        with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)
            if not self.subscribers:
                try:
                    self.process.kill()
                except Exception:
                    pass
                
def get_shared_stream(channel_id: int, stream_url: str) -> SharedStream:
    # Build your FFmpeg command, optionally enabling CUDA if available
    ffmpeg_cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-user_agent", "VLC/3.0.20-git LibVLC/3.0.20-git",
    ]

    # Insert hardware acceleration flags (before input) if configured
    accel_args = config.get("FFMPEG_ACCEL_ARGS", "") or ""
    if accel_args:
        ffmpeg_cmd.extend(accel_args.split())

    # Input and general options
    ffmpeg_cmd.extend([
        "-re", "-i", stream_url,
        "-max_muxing_queue_size", "1024",
    ])

    # Choose video codec: NVENC if configured, otherwise copy
    encoder = config.get("FFMPEG_ENCODER", "") or ""
    if encoder:
        ffmpeg_cmd.extend(["-c:v", encoder])
        # Apply sensible defaults for NVENC encoders
        if encoder.endswith("_nvenc"):
            ffmpeg_cmd.extend([
                "-preset", "p5",   # NVENC preset scale p1..p7 (speed..quality)
                "-rc", "vbr",
                "-cq", "23",
                "-b:v", "3M",
                "-maxrate", "6M",
            ])
    else:
        ffmpeg_cmd.extend(["-c:v", "copy"])  # no GPU / keep original video

    # Audio: transcode to AAC for compatibility
    ffmpeg_cmd.extend(["-c:a", "aac"])

    # Output as MPEG-TS to stdout
    ffmpeg_cmd.extend(["-f", "mpegts", "pipe:1"])

    with streams_lock:
        if channel_id in shared_streams and shared_streams[channel_id].is_running:
            return shared_streams[channel_id]
        shared_streams[channel_id] = SharedStream(channel_id, ffmpeg_cmd)
        return shared_streams[channel_id]

def clear_shared_stream(channel_id: int):
    """
    Kill and remove the shared stream for a given channel id (if one exists).
    """
    with streams_lock:
        if channel_id in shared_streams:
            try:
                shared_streams[channel_id].process.kill()
            except Exception:
                pass
            del shared_streams[channel_id]
