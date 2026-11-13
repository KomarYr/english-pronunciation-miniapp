"""Report rendering, shared by the CLI (plain text) and the Telegram bot (HTML).

Pure functions: take domain types / read models, return strings. No input,
no network, no storage.
"""

import html as html_module

from azure_mini_app import app
from azure_mini_app.domain import Attempt
from azure_mini_app.services.quota import QuotaStatus
from azure_mini_app.services.storage import ExerciseStat, UserStats

MARKS = {
    "Mispronunciation": ("✗", "said wrong"),
    "Omission": ("–", "skipped"),
    "Insertion": ("+", "extra"),
}


def format_seconds(seconds: float) -> str:
    minutes, sec = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} h {minutes} min"
    if minutes:
        return f"{minutes} min {sec} s"
    return f"{sec} s"


# --- plain text (CLI) -------------------------------------------------------


def format_report(attempt: Attempt) -> str:
    report = attempt.report
    lines = [
        "",
        f"You said: {report.recognized_text or '—'}",
        f"Pronunciation: {report.pronunciation_score:.0f}/100",
        f"  accuracy     {report.accuracy_score:>5.1f}",
        f"  fluency      {report.fluency_score:>5.1f}",
        f"  completeness {report.completeness_score:>5.1f}",
        f"  prosody      {report.prosody_score:>5.1f}",
        "",
    ]
    problem_words = [w for w in report.words if w.error_type in MARKS]
    if problem_words:
        lines.append("Words to work on:")
        for word in problem_words:
            symbol, label = MARKS[word.error_type]
            lines.append(
                f"  {word.word:<15} {symbol} {label:<12} {word.accuracy_score:.0f}"
            )
    else:
        lines.append("Every word recognized cleanly — nice.")
    if report.pronunciation_score < app.WEAK_SCORE_THRESHOLD:
        lines.append("Saved to your mistake bank — practice it again soon.")
    return "\n".join(lines)


def format_mistake_bank(stats: list[ExerciseStat]) -> str:
    if not stats:
        return "Mistake bank is empty — nothing weak so far."
    lines = ["Your weakest exercises (avg pronunciation score):"]
    for i, stat in enumerate(stats, start=1):
        lines.append(
            f"  {i}) [{stat.avg_pronunciation_score:>5.1f}] {stat.exercise.title}"
        )
    return "\n".join(lines)


def format_stats(user_stats: UserStats, quota_status: QuotaStatus) -> str:
    avg = (
        f"{user_stats.avg_pronunciation_score:.1f}"
        if user_stats.avg_pronunciation_score is not None
        else "—"
    )
    lines = [
        "",
        f"Attempts this profile: {user_stats.total_attempts}",
        f"Practice time (graded): {format_seconds(user_stats.total_audio_seconds)}",
        f"Average pronunciation:  {avg}",
        (
            f"Monthly audio budget:   {format_seconds(quota_status.used_seconds)} of "
            f"{format_seconds(quota_status.limit_seconds)} used"
        ),
    ]
    if quota_status.warn:
        lines.append("⚠ Over 80% of this month's audio budget is used.")
    return "\n".join(lines)


# --- HTML (Telegram) --------------------------------------------------------


def score_bar(score: float) -> str:
    """10-cell bar: █ for each full 10 points, ░ for the rest."""
    filled = round(score / 10)
    filled = max(filled, 0)
    return "█" * filled + "░" * (10 - filled)


def render_report_html(attempt: Attempt) -> str:
    """Full per-word report for Telegram. HTML-escapes user-generated text.

    Same information as Azure's assessment dashboard (overall, four-way
    breakdown, every word with phoneme scores) in our own style: symbols
    and bars instead of the donut chart and red/yellow/green palette.
    """
    report = attempt.report
    score = report.pronunciation_score
    lines = [
        f"🎤 <b>{html_module.escape(report.recognized_text or '—')}</b>",
        "",
        f"<b>Pronunciation: {score:.0f}/100</b>",
        f"<code>{score_bar(score)}</code>",
        "",
        f"  Accuracy     <code>{score_bar(report.accuracy_score)}</code> {report.accuracy_score:.0f}",
        f"  Fluency      <code>{score_bar(report.fluency_score)}</code> {report.fluency_score:.0f}",
        (
            f"  Completeness <code>{score_bar(report.completeness_score)}</code>"
            f" {report.completeness_score:.0f}"
        ),
        f"  Prosody      <code>{score_bar(report.prosody_score)}</code> {report.prosody_score:.0f}",
        "",
        "<b>Words</b> (in reading order, with phoneme scores):",
    ]
    for word in report.words:  # original spoken order, not sorted
        symbol, label = MARKS.get(word.error_type, ("✓", "clean"))
        lines.append(
            f"  {symbol} <code>{html_module.escape(word.word)}</code> — {word.accuracy_score:.0f} {label}"
        )
        for phoneme, phoneme_score in word.phonemes:
            lines.append(
                f"      <code>{html_module.escape(phoneme)}</code> {phoneme_score:.0f}"
            )
    lines.append("")
    has_problem_words = any(w.error_type in MARKS for w in report.words)
    if score < app.WEAK_SCORE_THRESHOLD:
        lines.append("📉 Saved to your mistake bank — practice it again soon.")
    elif not has_problem_words:
        lines.append("🎉 Every word recognized cleanly — nice!")
    else:
        lines.append("👍 Good overall score — work on the marked words above.")
    return "\n".join(lines)


def render_mistakes_html(stats: list[ExerciseStat]) -> str:
    if not stats:
        return "🎉 Mistake bank is empty — nothing weak so far."
    lines = ["<b>Your weakest exercises</b> (avg pronunciation score):"]
    for i, stat in enumerate(stats, start=1):
        title = html_module.escape(stat.exercise.title)
        lines.append(
            f"  {i}) <code>{score_bar(stat.avg_pronunciation_score)}</code> {title}"
        )
    return "\n".join(lines)


def render_stats_html(user_stats: UserStats, quota_status: QuotaStatus) -> str:
    avg = (
        f"{user_stats.avg_pronunciation_score:.1f}"
        if user_stats.avg_pronunciation_score is not None
        else "—"
    )
    lines = [
        "<b>Your stats</b>",
        "",
        f"  Attempts: <b>{user_stats.total_attempts}</b>",
        f"  Practice time (graded): {format_seconds(user_stats.total_audio_seconds)}",
        f"  Average pronunciation: {avg}",
        (
            f"  Monthly audio budget: {format_seconds(quota_status.used_seconds)} of"
            f" {format_seconds(quota_status.limit_seconds)} used"
        ),
    ]
    if quota_status.warn:
        lines.append("⚠️ Over 80% of this month's audio budget is used.")
    return "\n".join(lines)
