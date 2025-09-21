import subprocess
import threading
import queue
from .config import config  # access runtime ffmpeg/gpu decisions
import sqlite3

# --- FFmpeg profile management ---
# We keep a registry of named profiles. Each profile is a list of argument tokens
# (not including the initial 'ffmpeg' binary). Profiles must include an "-i" with
# a placeholder token "{input}" that will be replaced by the stream URL.

FFMPEG_DEFAULT_USER_AGENT = "VLC/3.0.20-git LibVLC/3.0.20-git"

_profile_registry: dict[str, list[str]] = {}


def register_ffmpeg_profile(name: str, args: list[str]) -> None:
    """Register or overwrite an ffmpeg profile.
    Args are tokens excluding the initial 'ffmpeg'. Must include '{input}'.
    """
    if "{input}" not in args:
        raise ValueError("Profile args must include '{input}' placeholder for the input URL")
    _profile_registry[name] = args


def list_ffmpeg_profiles() -> list[str]:
    return sorted(_profile_registry.keys())


def get_ffmpeg_profiles() -> list[dict]:
    """Return a list of profiles with their names and args (as a list of tokens)."""
    out = []
    for name, args in _profile_registry.items():
        out.append({"name": name, "args": list(args)})
    # Sort by name for stability
    out.sort(key=lambda x: x["name"].lower())
    return out


def delete_ffmpeg_profile(name: str) -> bool:
    """Delete a custom profile. Returns True if deleted.
    Built-in profiles (CPU, CUDA) cannot be deleted.
    If the deleted profile is currently selected, fall back to CPU.
    """
    protected = {"CPU", "CUDA"}
    if name in protected:
        return False
    if name in _profile_registry:
        del _profile_registry[name]
        # If selected was this profile, fall back to CPU
        if config.get("FFMPEG_PROFILE") == name:
            config["FFMPEG_PROFILE"] = "CPU"
        return True
    return False


def select_ffmpeg_profile(name: str) -> None:
    if name not in _profile_registry:
        raise KeyError(f"Unknown ffmpeg profile: {name}")
    config["FFMPEG_PROFILE"] = name


def get_selected_ffmpeg_profile_name() -> str:
    return config.get("FFMPEG_PROFILE") or "CPU"


def _ensure_builtin_profiles_registered() -> None:
    # Common prefix used by built-in profiles
    base_prefix = [
        "-hide_banner", "-loglevel", "error",
        "-user_agent", FFMPEG_DEFAULT_USER_AGENT,
    ]

    # CPU profile: copy video, transcode audio to AAC for compatibility
    cpu_args = base_prefix + [
        "-re", "-i", "{input}",
        "-max_muxing_queue_size", "1024",
        "-c:v", "copy",
        "-c:a", "aac",
        "-f", "mpegts", "pipe:1",
    ]

    # CUDA profile: enable hwaccel and use NVENC with reasonable defaults
    cuda_args = base_prefix + [
        "-hwaccel", "cuda", "-hwaccel_output_format", "cuda",
        "-re", "-i", "{input}",
        "-max_muxing_queue_size", "1024",
        "-c:v", "h264_nvenc",
        "-preset", "p5",  # p1..p7 speed..quality
        "-rc", "vbr",
        "-cq", "23",
        "-b:v", "3M",
        "-maxrate", "6M",
        "-c:a", "aac",
        "-f", "mpegts", "pipe:1",
    ]

    # Register built-ins
    _profile_registry.setdefault("CPU", cpu_args)
    _profile_registry.setdefault("CUDA", cuda_args)

    # Optionally bootstrap custom profiles from config, if provided as a dict
    # mapping profile name -> space-delimited arg string (excluding 'ffmpeg').
    custom_profiles = config.get("FFMPEG_CUSTOM_PROFILES")
    if isinstance(custom_profiles, dict):
        for pname, pargs in custom_profiles.items():
            if isinstance(pargs, str):
                try:
                    register_ffmpeg_profile(pname, pargs.split())
                except Exception as e:
                    print(f"[FFmpeg] Skipping invalid custom profile '{pname}': {e}")


_ensure_builtin_profiles_registered()


def build_ffmpeg_command(stream_url: str) -> list[str]:
    """Build the ffmpeg command for the currently selected profile.
    Falls back to 'CPU' if the selected profile is missing.
    """
    profile_name = get_selected_ffmpeg_profile_name()
    args = _profile_registry.get(profile_name)
    if not args:
        print(f"[FFmpeg] Unknown selected profile '{profile_name}', falling back to 'CPU'.")
        args = _profile_registry["CPU"]

    # Replace the '{input}' placeholder with the actual URL
    final_args: list[str] = []
    for tok in args:
        if tok == "{input}":
            final_args.append(stream_url)
        else:
            final_args.append(tok)

    # Allow a legacy escape hatch: if FFMPEG_CUSTOM_ARGS is present and the
    # selected profile is exactly 'CUSTOM', append those tokens.
    if profile_name.upper() == "CUSTOM":
        extra = config.get("FFMPEG_CUSTOM_ARGS")
        if isinstance(extra, str) and extra.strip():
            final_args.extend(extra.split())

    return ["ffmpeg"] + final_args

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
    # Build the FFmpeg command from the selected profile (CPU, CUDA, or custom)
    ffmpeg_cmd = build_ffmpeg_command(stream_url)

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

