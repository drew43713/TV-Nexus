import subprocess
from typing import Any


def _run_cmd(cmd: list[str]) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            text=True,
            timeout=10,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except FileNotFoundError as e:
        return 127, "", str(e)
    except subprocess.TimeoutExpired:
        return 124, "", "Command timed out"


def detect_cuda_support() -> dict[str, Any]:
    """Detect whether NVIDIA GPU and CUDA hwaccel are available to this container.

    Returns a dict with keys:
      - gpu_available: bool
      - nvidia_smi_rc: int
      - nvidia_smi_output: str
      - ffmpeg_hwaccels_rc: int
      - ffmpeg_hwaccels: list[str]
      - cuda_in_hwaccels: bool
    """
    smi_rc, smi_out, smi_err = _run_cmd(["nvidia-smi"])  # requires `--gpus all` in Docker
    gpu_available = smi_rc == 0

    ff_rc, ff_out, ff_err = _run_cmd(["ffmpeg", "-hide_banner", "-hwaccels"])
    hw_lines: list[str] = []
    if ff_rc == 0 and ff_out:
        hw_lines = [
            ln.strip()
            for ln in ff_out.splitlines()
            if ln.strip() and not ln.lower().startswith("hardware acceleration methods")
        ]
    cuda_in = any(ln.lower() == "cuda" for ln in hw_lines)

    return {
        "gpu_available": bool(gpu_available),
        "nvidia_smi_rc": smi_rc,
        "nvidia_smi_output": smi_out if smi_out else smi_err,
        "ffmpeg_hwaccels_rc": ff_rc,
        "ffmpeg_hwaccels": hw_lines,
        "cuda_in_hwaccels": bool(cuda_in),
    }
