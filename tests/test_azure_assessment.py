"""Assessment service tests: translation layer, exceptions, WAV duration.

The real SDK call path is not covered here (needs Azure credentials);
the translation helpers are duck-typed on purpose so fakes work.
"""

import wave
from types import SimpleNamespace

import pytest

from speech_project_azure.services.azure_assessment import (
    AssessmentCanceled,
    AssessmentError,
    NoSpeechDetected,
    _report_from_result,
    _speech_duration_seconds,
    _wav_duration_seconds,
    _words_from_assessment,
)


def fake_pron_result() -> SimpleNamespace:
    return SimpleNamespace(
        accuracy_score=92.0,
        fluency_score=88.0,
        completeness_score=100.0,
        prosody_score=75.0,
        pronunciation_score=89.0,
        words=[
            SimpleNamespace(
                word="She",
                accuracy_score=95.0,
                error_type="None",
                phonemes=[
                    SimpleNamespace(phoneme="ʃ", accuracy_score=94.0),
                    SimpleNamespace(phoneme="i", accuracy_score=96.0),
                ],
            ),
            SimpleNamespace(
                word="sells",
                accuracy_score=90.0,
                error_type="None",
                phonemes=[SimpleNamespace(phoneme="s", accuracy_score=90.0)],
            ),
            SimpleNamespace(
                word="shells",
                accuracy_score=60.0,
                error_type="Mispronunciation",
                phonemes=[],
            ),
            SimpleNamespace(
                word="six",
                accuracy_score=0.0,
                error_type="Omission",
                phonemes=[],
            ),
        ],
    )


def fake_sdk_result() -> SimpleNamespace:
    return SimpleNamespace(text="She sells six silver shells.")


class TestWordMapping:
    def test_maps_all_word_fields(self):
        words = _words_from_assessment(fake_pron_result())
        assert words[0].word == "She"
        assert words[0].accuracy_score == 95.0
        assert words[0].error_type == "None"

    def test_maps_phoneme_scores_in_order(self):
        words = _words_from_assessment(fake_pron_result())
        assert words[0].phonemes == (("ʃ", 94.0), ("i", 96.0))
        assert words[1].phonemes == (("s", 90.0),)
        assert words[2].phonemes == ()  # no phoneme data → empty, not an error

    def test_missing_phonemes_attribute_is_tolerated(self):
        # very old SDK objects (or lazy fakes) may lack .phonemes entirely
        pron = SimpleNamespace(
            words=[SimpleNamespace(word="word", accuracy_score=80.0, error_type="None")]
        )
        words = _words_from_assessment(pron)
        assert words[0].phonemes == ()

    def test_maps_all_error_types(self):
        pron = SimpleNamespace(
            words=[
                SimpleNamespace(word="ok", accuracy_score=90.0, error_type="None"),
                SimpleNamespace(
                    word="miz", accuracy_score=40.0, error_type="Mispronunciation"
                ),
                SimpleNamespace(word="", accuracy_score=0.0, error_type="Omission"),
                SimpleNamespace(
                    word="extra", accuracy_score=0.0, error_type="Insertion"
                ),
            ]
        )
        words = _words_from_assessment(pron)
        assert [w.error_type for w in words] == [
            "None",
            "Mispronunciation",
            "Omission",
            "Insertion",
        ]

    def test_empty_words(self):
        pron = SimpleNamespace(words=[])
        assert _words_from_assessment(pron) == ()


class TestReportMapping:
    def test_full_report_roundtrip(self):
        sdk_result, pron = fake_sdk_result(), fake_pron_result()
        report = _report_from_result(sdk_result, pron)
        assert report.recognized_text == "She sells six silver shells."
        assert report.pronunciation_score == 89.0
        assert report.accuracy_score == 92.0
        assert report.fluency_score == 88.0
        assert report.completeness_score == 100.0
        assert report.prosody_score == 75.0
        assert len(report.words) == 4
        assert report.words[3].error_type == "Omission"


class TestExceptions:
    def test_hierarchy(self):
        assert issubclass(NoSpeechDetected, AssessmentError)
        assert issubclass(AssessmentCanceled, AssessmentError)
        assert issubclass(AssessmentError, Exception)

    def test_canceled_carries_details(self):
        error = AssessmentCanceled("Error", "Invalid subscription key")
        assert error.reason == "Error"
        assert error.error_details == "Invalid subscription key"
        assert "Invalid subscription key" in str(error)

    def test_canceled_without_details(self):
        error = AssessmentCanceled("EndOfStream")
        assert error.error_details == ""
        assert "EndOfStream" in str(error)


class TestWavDuration:
    def write_wav(self, path, seconds: int = 1, rate: int = 16000):
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(rate)
            wav.writeframes(b"\x00\x00" * rate * seconds)

    def test_one_second_mono_16k(self, tmp_path):
        wav = tmp_path / "a.wav"
        self.write_wav(wav, seconds=1)
        assert _wav_duration_seconds(wav) == pytest.approx(1.0)

    def test_two_seconds(self, tmp_path):
        wav = tmp_path / "b.wav"
        self.write_wav(wav, seconds=2)
        assert _wav_duration_seconds(wav) == pytest.approx(2.0)


class TestSpeechDuration:
    def test_uses_result_duration_when_present(self):
        result = SimpleNamespace(duration=35_000_000)  # 3.5 s in 100 ns ticks
        assert _speech_duration_seconds(result, wall_clock=12.0) == pytest.approx(3.5)

    def test_falls_back_to_wall_clock_without_duration(self):
        result = SimpleNamespace(duration=0)
        assert _speech_duration_seconds(result, wall_clock=12.0) == 12.0
        assert _speech_duration_seconds(SimpleNamespace(), wall_clock=7.5) == 7.5
