/* Stats screen: attempts, scores and the monthly audio budget gauge. */

import { stats as fetchStats, ApiError } from "../api";
import { formatSeconds, el } from "../ui";
import type { Quota } from "../types";

function quotaBar(quota: Quota): HTMLElement {
  const percent = Math.min(quota.fraction_used * 100, 100);
  const kind = quota.exhausted ? "bad" : quota.warn ? "okay" : "good";
  return el(
    "div",
    { class: "card" },
    el("h4", { class: "card-title", text: "Audio budget (monthly)" }),
    el("div", { class: "bar big" },
      el("div", { class: `bar-fill ${kind}`, style: `width:${percent}%` })),
    el("p", { class: "quota-text" },
      `${formatSeconds(quota.used_seconds)} of ${formatSeconds(quota.limit_seconds)} used — `,
      el("strong", { text: `${formatSeconds(quota.remaining_seconds)} left` })),
    quota.warn && !quota.exhausted
      ? el("p", { class: "quota-warning", text: "⚠ Budget almost gone — be picky with attempts." })
      : null,
    quota.exhausted
      ? el("p", { class: "quota-warning", text: "🚫 Practice is blocked until the 1st of next month." })
      : null,
  );
}

function statRow(label: string, value: string): HTMLElement {
  return el("div", { class: "stat-row" },
    el("span", { class: "stat-label", text: label }),
    el("span", { class: "stat-value", text: value }),
  );
}

export async function renderStats(target: HTMLElement): Promise<void> {
  target.replaceChildren(el("h2", { text: "Stats" }), el("p", { class: "muted", text: "Loading…" }));
  let body: Awaited<ReturnType<typeof fetchStats>>;
  try {
    body = await fetchStats();
  } catch (error) {
    if (error instanceof ApiError && error.code === "not_allowed") {
      target.append(el("div", { class: "card center" }, el("p", { text: "You are not allowed to use this trainer yet." })));
      return;
    }
    target.append(
      el("div", { class: "card center" },
        el("p", { text: "Could not load stats." }),
        el("p", { class: "muted", text: error instanceof Error ? error.message : "" })),
    );
    return;
  }

  const { stats, quota } = body;
  target.append(
    el("div", { class: "card" },
      el("h4", { class: "card-title", text: "Practice" }),
      statRow("Attempts", String(stats.total_attempts)),
      statRow("Practice time", formatSeconds(stats.total_audio_seconds)),
      statRow("Avg pronunciation", stats.avg_pronunciation_score === null ? "—" : `${stats.avg_pronunciation_score.toFixed(1)}/100`),
      statRow("Avg accuracy", stats.avg_accuracy_score === null ? "—" : `${stats.avg_accuracy_score.toFixed(1)}/100`),
    ),
    quotaBar(quota),
  );
}
