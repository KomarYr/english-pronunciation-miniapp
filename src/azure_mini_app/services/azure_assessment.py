"""Azure Speech SDK adapter — the only module allowed to import speechsdk.

Takes audio (a 16 kHz mono WAV file, or None for the microphone) plus a
reference text, and returns a domain ScoreReport with the audio seconds
consumed (the quota source of truth). One recognizer per attempt — safe
to call from worker threads.

Translation helpers are duck-typed so unit tests can exercise them with
fakes instead of real SDK objects.
"""

import time
import wave
from pathlib import Path
from typing import cast

import azure.cognitiveservices.speech as speechsdk

from azure_mini_app.domain import ScoreReport, WordScore
from azure_mini_app.settings import (
    language,
    speech_endpoint,
    speech_key,
    speech_region,
)


class AssessmentError(Exception):
    """Base class for assessment failures."""


class NoSpeechDetected(AssessmentError):
    """The recognizer returned NoMatch — nothing usable was said.

    audio_seconds still carries the consumed audio: Azure bills it even
    when nothing was recognized, so callers must log it for the quota.
    """

    def __init__(self, audio_seconds: float):
        super().__init__("No speech could be recognized — try again.")
        self.audio_seconds = audio_seconds


class AssessmentCanceled(AssessmentError):
    """The service canceled recognition (bad key, quota, network...)."""

    def __init__(
        self, reason: str, error_details: str = "", audio_seconds: float = 0.0
    ):
        message = f"Assessment canceled: {reason}"
        if error_details:
            message += f" — {error_details}"
        super().__init__(message)
        self.reason = reason
        self.error_details = error_details
        self.audio_seconds = audio_seconds


# --- translation layer (duck-typed, unit-testable without Azure) ----------


def _words_from_assessment(pron) -> tuple[WordScore, ...]:
    """pron: speechsdk.PronunciationAssessmentResult (or a duck-typed fake)."""
    return tuple(
        WordScore(
            word=w.word,
            accuracy_score=w.accuracy_score,
            error_type=w.error_type,
            phonemes=tuple(
                (p.phoneme, p.accuracy_score) for p in getattr(w, "phonemes", ())
            ),
        )
        for w in pron.words
    )


def _report_from_result(sdk_result, pron) -> ScoreReport:
    """sdk_result: SpeechRecognitionResult; pron: PronunciationAssessmentResult."""
    return ScoreReport(
        recognized_text=sdk_result.text,
        accuracy_score=pron.accuracy_score,
        fluency_score=pron.fluency_score,
        completeness_score=pron.completeness_score,
        prosody_score=pron.prosody_score,
        pronunciation_score=pron.pronunciation_score,
        words=_words_from_assessment(pron),
    )


# --- public API ------------------------------------------------------------


def _build_speech_config() -> speechsdk.SpeechConfig:
    key = speech_key
    if not key:
        raise AssessmentError("Missing SUBSCRIPTION_KEY in .env")
    if speech_endpoint:
        return speechsdk.SpeechConfig(subscription=key, endpoint=speech_endpoint)
    if speech_region:
        return speechsdk.SpeechConfig(subscription=key, region=speech_region)
    raise AssessmentError("Set SERVICE_REGION or SERVICE_ENDPOINT in .env")


def _build_pronunciation_config(
    reference_text: str,
) -> speechsdk.PronunciationAssessmentConfig:
    pron_config = speechsdk.PronunciationAssessmentConfig(
        reference_text=reference_text,
        grading_system=speechsdk.PronunciationAssessmentGradingSystem.HundredMark,
        granularity=speechsdk.PronunciationAssessmentGranularity.Phoneme,
        enable_miscue=True,
    )
    pron_config.enable_prosody_assessment()
    return pron_config


def _wav_duration_seconds(path: Path) -> float:
    with wave.open(str(path)) as wav:
        frames = wav.getnframes()
        rate = wav.getframerate()
    return frames / rate if rate else 0.0


TICKS_PER_SECOND = 10_000_000  # SDK durations are in 100 ns ticks


def _speech_duration_seconds(sdk_result, wall_clock: float) -> float:
    """Actual speech length from the result, when the SDK reports it.

    Mic sessions overcount wall-clock (silence timeout + dead air before
    reading starts); the recognized result's own duration covers only the
    speech itself. Falls back to wall-clock when there is no duration
    (NoMatch / Canceled — Azure listened the whole window, nothing to read).
    """
    duration = getattr(sdk_result, "duration", None)
    if duration and duration > 0:
        return duration / TICKS_PER_SECOND
    return wall_clock


def assess(
    audio: Path | None,
    reference_text: str,
    language: str = language,
) -> tuple[ScoreReport, float]:
    """Assess one spoken attempt against reference_text.

    audio=None uses the default microphone; otherwise `audio` is a 16 kHz
    mono WAV file (as produced by the Telegram voice-note conversion).

    Returns (report, audio_seconds). audio_seconds is exact for files; for
    the microphone it is the recognized speech length (result.duration),
    falling back to wall-clock only for silent attempts — silence timeout
    and dead air are not billed as speech.
    Azure bills every audio transaction, including silent ones: on
    NoSpeechDetected / AssessmentCanceled the exception carries
    audio_seconds and the caller MUST log it via storage.add_audio_usage.
    """
    config = _build_speech_config()
    # A longer break counts as end-of-utterance (reading pace, not conversation).
    config.set_property(
        speechsdk.PropertyId.SpeechServiceConnection_EndSilenceTimeoutMs, "3000"
    )

    audio_config = (
        speechsdk.AudioConfig(filename=str(audio)) if audio is not None else None
    )
    recognizer = speechsdk.SpeechRecognizer(
        speech_config=config, language=language, audio_config=audio_config
    )
    pronunciation_config = _build_pronunciation_config(reference_text)
    pronunciation_config.apply_to(recognizer)

    duration_clock = time.monotonic()
    # The SDK's ResultFuture.get() is untyped and its implementation ends
    # in `... else None`, so checkers infer `Unknown | None` and flag every
    # result.* access. recognize_once() always yields a
    # SpeechRecognitionResult — say so once, at this boundary.
    result = cast(
        speechsdk.SpeechRecognitionResult,
        recognizer.recognize_once_async().get(),
    )
    wall_clock = time.monotonic() - duration_clock
    audio_seconds = _wav_duration_seconds(audio) if audio is not None else wall_clock

    if result.reason == speechsdk.ResultReason.NoMatch:
        raise NoSpeechDetected(audio_seconds)
    if result.reason == speechsdk.ResultReason.Canceled:
        details = result.cancellation_details
        raise AssessmentCanceled(
            str(details.reason), details.error_details, audio_seconds
        )
    if result.reason != speechsdk.ResultReason.RecognizedSpeech:
        raise AssessmentError(f"Unexpected recognizer result: {result.reason}")

    mic_seconds = (
        _speech_duration_seconds(result, wall_clock) if audio is None else audio_seconds
    )
    pron_result = speechsdk.PronunciationAssessmentResult(result)
    # Azure can return RecognizedSpeech with *no* assessment scores when
    # the audio is empty/inaudible yet not silent enough for NoMatch —
    # the SDK's float type hints lie. Surface that as the typed
    # no-speech error instead of a None-scored report (which the
    # attempts table's NOT NULL score columns rightly reject).
    scored = (
        pron_result.accuracy_score,
        pron_result.fluency_score,
        pron_result.completeness_score,
        pron_result.prosody_score,
        pron_result.pronunciation_score,
    )
    if any(score is None for score in scored):
        raise NoSpeechDetected(mic_seconds)
    return _report_from_result(result, pron_result), mic_seconds
