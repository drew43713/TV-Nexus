import subprocess
import threading
import queue
from .config import DB_FILE  # if needed elsewhere
import sqlite3

streams_lock = threading.Lock()
shared_streams = {}

class SharedStream:
    def __init__(self, ffmpeg_cmd):
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
                print("No chunk received from FFmpeg; ending broadcast.")
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
            print("FFmpeg stderr:", stderr_output.decode('utf-8', errors='ignore'))

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
    print(f"Creating shared stream for channel {channel_id} with URL: {stream_url}")
    ffmpeg_cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-user_agent", "VLC/3.0.20-git LibVLC/3.0.20-git",
        "-re", "-i", stream_url,
        "-max_muxing_queue_size", "1024",
        "-c:v", "copy", "-c:a", "aac",
        "-preset", "ultrafast",
        "-f", "mpegts", "pipe:1"
    ]
    with streams_lock:
        if channel_id in shared_streams and shared_streams[channel_id].is_running:
            return shared_streams[channel_id]
        shared_streams[channel_id] = SharedStream(ffmpeg_cmd)
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