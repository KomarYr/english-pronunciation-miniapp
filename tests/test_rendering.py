"""Rendering tests: plain-text (CLI) and HTML (Telegram) formatters."""

from datetime import UTC, datetime

from speech_project_azure.domain import Attempt, ScoreReport, WordScore
from speech_project_azure.interactions.rendering import (
    format_mistake_bank,
    format_report,
    format_seconds,
    format_stats,
    render_mistakes_html,
    render_report_html,
    render_stats_html,
    score_bar,
)
from speech_project_azure.services.quota import MONTHLY_LIMIT_SECONDS, QuotaStatus
from speech_project_azure.services.storage import UserStats


def make_attempt(pronunciation: float, words: tuple) -> Attempt:
    return Attempt(
        user_id=1,
        exercise_id="s-vs-sh-sentence-1",
        reference_text="She sells shells",
        created_at=datetime(2026, 11, 10, tzinfo=UTC),
        audio_seconds=4.2,
        report=ScoreReport(
            recognized_text="She sells shells",
            accuracy_score=70.0,
            fluency_score=80.0,
            completeness_score=90.0,
            prosody_score=60.0,
            pronunciation_score=pronunciation,
            words=words,
        ),
    )


class TestFormatSeconds:
    def test_seconds_only(self):
        assert format_seconds(59) == "59 s"

    def test_minutes(self):
        assert format_seconds(83) == "1 min 23 s"

    def test_hours(self):
        assert format_seconds(5010) == "1 h 23 min"


class TestFormatReportPlain:
    def test_flags_problem_words(self):
        words = (
            WordScore("She", 90.0, "None"),
            WordScore("sells", 40.0, "Mispronunciation"),
            WordScore("shells", 0.0, "Omission"),
        )
        text = format_report(make_attempt(65.0, words))
        assert "sells" in text and "said wrong" in text
        assert "shells" in text and "skipped" in text
        assert "mistake bank" in text  # 65 < 70

    def test_clean_words(self):
        text = format_report(make_attempt(98.0, (WordScore("Perfect", 99.0, "None"),)))
        assert "Every word recognized cleanly" in text
        assert "mistake bank" not in text


class TestFormatMistakeBankPlain:
    def test_empty(self):
        assert "empty" in format_mistake_bank([])

    def test_ranked(self):
        from speech_project_azure import exercises as catalog
        from speech_project_azure.services.storage import ExerciseStat

        stat = ExerciseStat(
            exercise=catalog.get_exercise("s-vs-sh-sentence-1"),
            avg_pronunciation_score=55.5,
            attempt_count=2,
            last_attempted=datetime(2026, 11, 10, tzinfo=UTC),
        )
        text = format_mistake_bank([stat])
        assert "s-vs-sh" not in text  # title shown, not the id
        assert "55.5" in text


class TestFormatStatsPlain:
    def test_includes_warning_at_80_percent(self):
        user_stats = UserStats(
            user_id=1,
            total_attempts=3,
            total_audio_seconds=125.0,
            avg_pronunciation_score=88.0,
            avg_accuracy_score=90.0,
        )
        status = QuotaStatus(
            used_seconds=MONTHLY_LIMIT_SECONDS * 0.9,
            limit_seconds=MONTHLY_LIMIT_SECONDS,
        )
        text = format_stats(user_stats, status)
        assert "3" in text
        assert "⚠" in text

    def test_no_warning_when_low_usage(self):
        user_stats = UserStats(1, 0, 0.0, None, None)
        status = QuotaStatus(used_seconds=10.0, limit_seconds=MONTHLY_LIMIT_SECONDS)
        assert "⚠" not in format_stats(user_stats, status)


class TestScoreBar:
    def test_bars(self):
        assert score_bar(100.0) == "█" * 10
        assert score_bar(0.0) == "░" * 10
        assert score_bar(82.0) == "█" * 8 + "░" * 2
        assert score_bar(50.0) == "█" * 5 + "░" * 5


class TestRenderReportHtml:
    def test_structure_and_bars(self):
        words = (
            WordScore("She", 90.0, "None"),
            WordScore("sells", 40.0, "Mispronunciation"),
            WordScore("shells", 0.0, "Omission"),
        )
        text = render_report_html(make_attempt(65.0, words))
        assert "<b>Pronunciation: 65/100</b>" in text
        assert "█" in text and "░" in text
        assert "said wrong" in text and "skipped" in text
        assert "<code>sells</code>" in text
        assert "mistake bank" in text

    def test_escapes_user_generated_text(self):
        attempt = make_attempt(
            95.0, (WordScore("oops<script>", 50.0, "Mispronunciation"),)
        )
        text = render_report_html(attempt)
        assert "<script>" not in text
        assert "&lt;script&gt;" in text

    def test_clean_words_celebrates(self):
        text = render_report_html(
            make_attempt(98.0, (WordScore("Perfect", 99.0, "None"),))
        )
        assert "🎉" in text
        assert "mistake bank" not in text


class TestRenderMistakesHtml:
    def test_empty_bank(self):
        assert "empty" in render_mistakes_html([])

    def test_lists_titles_with_bars(self):
        from speech_project_azure import exercises as catalog
        from speech_project_azure.services.storage import ExerciseStat

        stat = ExerciseStat(
            exercise=catalog.get_exercise("s-vs-sh-sentence-1"),
            avg_pronunciation_score=48.0,
            attempt_count=1,
            last_attempted=datetime(2026, 11, 10, tzinfo=UTC),
        )
        text = render_mistakes_html([stat])
        assert "1)" in text and "█" in text
        assert "<b>" in text


class TestRenderStatsHtml:
    def test_renders_budget_and_warning(self):
        user_stats = UserStats(
            user_id=1,
            total_attempts=5,
            total_audio_seconds=300.0,
            avg_pronunciation_score=84.0,
            avg_accuracy_score=86.0,
        )
        status = QuotaStatus(
            used_seconds=MONTHLY_LIMIT_SECONDS * 0.85,
            limit_seconds=MONTHLY_LIMIT_SECONDS,
        )
        text = render_stats_html(user_stats, status)
        assert "<b>Your stats</b>" in text
        assert "⚠️" in text
        assert "4 h" in text  # 85% of 5 h budget shown in hours
