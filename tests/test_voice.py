"""Voice conversion tests: real ffmpeg conversion, missing binary, bad input."""

import subprocess
import wave
from pathlib import Path

import pytest

from speech_project_azure.services.voice import VoiceConversionError, ogg_to_wav


def ffmpeg_available() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except FileNotFoundError, subprocess.CalledProcessError:
        return False
    return True


def make_sine_ogg(path: Path, seconds: int = 1) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            "-ac",
            "1",
            "-ar",
            "48000",
            str(path),
        ],
        capture_output=True,
        check=True,
    )


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not installed")
class TestRealConversion:
    def test_ogg_to_16k_mono_wav(self, tmp_path):
        ogg = tmp_path / "voice.ogg"
        make_sine_ogg(ogg)
        wav = ogg_to_wav(ogg, tmp_path / "out")
        assert wav.exists() and wav.suffix == ".wav"
        with wave.open(str(wav)) as handle:
            assert handle.getframerate() == 16000
            assert handle.getnchannels() == 1
            assert handle.getnframes() / handle.getframerate() == pytest.approx(
                1.0, abs=0.1
            )

    def test_converted_file_independent_of_source(self, tmp_path):
        ogg = tmp_path / "voice.ogg"
        make_sine_ogg(ogg)
        wav = ogg_to_wav(ogg, tmp_path / "out")
        ogg.unlink()
        assert wav.exists()  # lives in target_dir, not beside the source


class TestFailures:
    def test_missing_ffmpeg(self, tmp_path, monkeypatch):
        def raise_not_found(*args, **kwargs):
            raise FileNotFoundError("ffmpeg")

        monkeypatch.setattr(
            "speech_project_azure.services.voice.subprocess.run", raise_not_found
        )
        with pytest.raises(VoiceConversionError, match="ffmpeg not found"):
            ogg_to_wav(tmp_path / "anything.ogg", tmp_path)

    def test_bad_input_file(self, tmp_path):
        garbage = tmp_path / "garbage.ogg"
        garbage.write_bytes(b"this is not an audio file at all")
        with pytest.raises(VoiceConversionError, match="failed"):
            ogg_to_wav(garbage, tmp_path)
