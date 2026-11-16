"""Regression: Azure can return RecognizedSpeech with *no* assessment
scores (empty/inaudible audio that never triggered NoMatch). The SDK's
PronunciationAssessmentResult then carries None scores despite its float
type hints, which used to flow straight into attempts.*_score NOT NULL
and blow up the API with a 500 (seen live: "NOT NULL constraint failed:
attempts.prosody_score").

The fake SDK here replicates exactly that shape; the fix contract is
that unscorable-but-recognized audio surfaces as the typed
NoSpeechDetected (quota-ledger 'no_speech'), never as a None-scored
ScoreReport.
"""

from types import SimpleNamespace

import pytest

import azure_mini_app.services.azure_assessment as mod
from azure_mini_app.services.azure_assessment import NoSpeechDetected


def fake_speechsdk_with_none_scores(result_text: str) -> SimpleNamespace:
    """SDK stub whose pronunciation result has None scores, as Azure
    returns for recognized-but-unscorable audio."""
    fake_result = SimpleNamespace(
        text=result_text, duration=0, reason="recognized"
    )

    def make_recognizer(**kwargs):
        return SimpleNamespace(
            recognize_once_async=lambda: SimpleNamespace(get=lambda: fake_result)
        )

    return SimpleNamespace(
        PropertyId=SimpleNamespace(SpeechServiceConnection_EndSilenceTimeoutMs="end-silence"),
        SpeechConfig=lambda subscription, region: SimpleNamespace(
            set_property=lambda *args: None
        ),
        SpeechRecognitionResult=type("SpeechRecognitionResult", (), {}),
        SpeechRecognizer=make_recognizer,
        ResultReason=SimpleNamespace(
            RecognizedSpeech="recognized", NoMatch="nomatch", Canceled="canceled"
        ),
        PronunciationAssessmentConfig=lambda **kwargs: SimpleNamespace(
            apply_to=lambda recognizer: None,
            enable_prosody_assessment=lambda: None,
        ),
        PronunciationAssessmentGradingSystem=SimpleNamespace(HundredMark="hundred"),
        PronunciationAssessmentGranularity=SimpleNamespace(Phoneme="phoneme"),
        PronunciationAssessmentResult=lambda result: SimpleNamespace(
            accuracy_score=0.0,
            fluency_score=None,
            completeness_score=0.0,
            prosody_score=None,
            pronunciation_score=None,
            words=[],
        ),
    )


class TestUnscorableAudio:
    def test_recognized_but_unscored_raises_no_speech(self, monkeypatch):
        monkeypatch.setattr(mod, "speechsdk", fake_speechsdk_with_none_scores(""))
        monkeypatch.setattr(mod, "speech_key", "fake-key")
        monkeypatch.setattr(mod, "speech_region", "fake-region")
        monkeypatch.setattr(mod, "speech_endpoint", None)

        with pytest.raises(NoSpeechDetected):
            mod.assess(None, "She sells six silver shells.")

    def test_unscored_with_text_also_raises_no_speech(self, monkeypatch):
        # Azure recognized *something* (noise) but scored nothing usable.
        monkeypatch.setattr(
            mod, "speechsdk", fake_speechsdk_with_none_scores("she sells")
        )
        monkeypatch.setattr(mod, "speech_key", "fake-key")
        monkeypatch.setattr(mod, "speech_region", "fake-region")
        monkeypatch.setattr(mod, "speech_endpoint", None)

        with pytest.raises(NoSpeechDetected):
            mod.assess(None, "She sells six silver shells.")
