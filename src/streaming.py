import subprocess
import threading
import queue
from .config import config  # access runtime ffmpeg/gpu decisions
import sqlite3
import shlex

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
                    register_ffmpeg_profile(pname, shlex.split(pargs))
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
            final_args.extend(shlex.split(extra))

    cmd = ["ffmpeg"] + final_args

    # Print the final command unconditionally for easier diagnostics
    quoted = " ".join(shlex.quote(t) for t in cmd)
    print("[FFmpeg] Command:", quoted, flush=True)

    return cmd

streams_lock = threading.Lock()
shared_streams = {}

class SharedStream:
    def __init__(self, channel_id, ffmpeg_cmd):
        self.channel_id = channel_id
        self.ffmpeg_cmd = ffmpeg_cmd
        print(f"[Stream] Starting channel {channel_id}", flush=True)
        self.process = subprocess.Popen(
            ffmpeg_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=10**8
        )
        self.subscribers = []
        self.lock = threading.Lock()
        self.is_running = True
        self.end_reason = None
        self.broadcast_thread = threading.Thread(target=self._broadcast)
        self.broadcast_thread.daemon = True
        self.broadcast_thread.start()

    def _broadcast(self):
        end_cause = None
        while True:
            chunk = self.process.stdout.read(1024)
            if not chunk:
                end_cause = "eof"
                break
            with self.lock:
                for q in self.subscribers:
                    q.put(chunk)
        self.is_running = False
        with self.lock:
            for q in self.subscribers:
                q.put(None)
        # Gather process exit code and stderr for diagnostics
        rc = None
        try:
            rc = self.process.wait(timeout=1)
        except Exception:
            rc = self.process.poll()

        stderr_output = b""
        try:
            stderr_output = self.process.stderr.read() or b""
        except Exception:
            pass

        # Determine reason: explicit end_reason set elsewhere, or EOF, otherwise unknown
        reason = self.end_reason or ("eof" if end_cause == "eof" else "unknown")
        print(f"[Stream] Channel {self.channel_id} ended (reason: {reason}, exit_code: {rc})", flush=True)

        if stderr_output:
            stderr_text = stderr_output.decode("utf-8", errors="ignore")
            lines = [ln for ln in stderr_text.strip().splitlines() if ln.strip()]
            tail_count = min(10, len(lines))
            if tail_count > 0:
                tail = "\n".join(lines[-tail_count:])
                print(f"[FFmpeg stderr][channel {self.channel_id}] last {tail_count} lines:\n{tail}", flush=True)

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
                self.end_reason = "no subscribers"
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
            print(f"[Stream] Clearing channel {channel_id}", flush=True)
            try:
                shared_streams[channel_id].process.kill()
            except Exception:
                pass
            del shared_streams[channel_id]

