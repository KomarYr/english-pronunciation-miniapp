"""Voice-note conversion: Telegram OGG/Opus → 16 kHz mono WAV for Azure.

The only place that shells out to ffmpeg. Kept small and typed so the
Telegram adapter can render failures as friendly messages.
"""

import subprocess
from pathlib import Path


class VoiceConversionError(Exception):
    """ffmpeg is missing, timed out, or rejected the file."""


def ogg_to_wav(source: Path, target_dir: Path) -> Path:
    """Convert an audio file to 16 kHz mono WAV (Azure's expected format).

    Returns the path of the converted file inside target_dir. Raises
    VoiceConversionError when conversion fails for any reason.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / (source.stem + ".wav")
    command = [
        "ffmpeg",
        "-y",  # overwrite
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-ac",
        "1",  # mono
        "-ar",
        "16000",  # Azure's preferred sample rate
        str(target),
    ]
    try:
        process = subprocess.run(
            command, check=False, capture_output=True, text=True, timeout=60
        )
    except FileNotFoundError:
        raise VoiceConversionError(
            "ffmpeg not found — install it first (e.g. sudo apt install ffmpeg)"
        ) from None
    except subprocess.TimeoutExpired:
        raise VoiceConversionError(
            "Audio conversion took too long — try a shorter recording."
        ) from None
    if process.returncode != 0 or not target.exists():
        detail = process.stderr.strip()[:200]
        raise VoiceConversionError(
            f"Audio conversion failed: {detail or 'unknown error'}"
        )
    return target
