/* Report screen: the per-attempt score card the whole app exists for. */

import type { Attempt, WordErrorType, WordScore } from "../types";
import { el } from "../ui";

const ERROR_BADGES: Record<WordErrorType, { label: string; class: string } | null> = {
  None: null,
  Mispronunciation: { label: "said wrong", class: "badge-bad" },
  Omission: { label: "skipped", class: "badge-skipped" },
  Insertion: { label: "extra", class: "badge-extra" },
};

function scoreColor(score: number): string {
  if (score >= 90) return "good";
  if (score >= 70) return "okay";
  return "bad";
}

function wordRow(word: WordScore): HTMLElement {
  const badge = ERROR_BADGES[word.error_type];
  const row = el("li", { class: "word-row" });
  row.append(
    el("span", { class: "word" }, word.word),
    badge === null
      ? el("span", { class: "badge badge-ok", text: "✓" })
      : el("span", { class: `badge ${badge.class}`, text: badge.label }),
    el("span", { class: `word-score ${scoreColor(word.accuracy_score)}` }, String(Math.round(word.accuracy_score))),
  );
  if (word.phonemes.length > 0) {
    row.append(
      el(
        "div",
        { class: "phonemes" },
        ...word.phonemes.map(([phoneme, score]) =>
          el("span", { class: "chip phoneme" }, `/${phoneme}/ ${Math.round(score)}`),
        ),
      ),
    );
  }
  return row;
}

export interface ReportActions {
  onBack: () => void;
  onCatalog: () => void;
}

export function renderReport(
  target: HTMLElement,
  attempt: Attempt,
  actions: ReportActions,
): void {
  document.body.classList.remove("recording"); // flow finished, un-modal the UI
  const report = attempt.report;
  const breakdown: [string, number][] = [
    ["Accuracy", report.accuracy_score],
    ["Fluency", report.fluency_score],
    ["Completeness", report.completeness_score],
    ["Prosody", report.prosody_score],
  ];

  const view = el(
    "section",
    { class: "report-view" },
    el(
      "div",
      { class: "card center" },
      el("p", { class: "muted", text: "You said" }),
      el("p", { class: "recognized", text: report.recognized_text }),
      el(
        "div",
        { class: "hero-score" },
        el("span", { class: `hero-number ${scoreColor(report.pronunciation_score)}` }, String(Math.round(report.pronunciation_score))),
        el("span", { class: "hero-label", text: "Pronunciation" }),
      ),
    ),
    el(
      "div",
      { class: "card breakdown" },
      ...breakdown.map(([label, score]) =>
        el("div", { class: "breakdown-row" },
          el("span", { class: "breakdown-label", text: label }),
          el("div", { class: "bar" }, el("div", { class: `bar-fill ${scoreColor(score)}`, style: `width:${score}%` })),
          el("span", { class: "breakdown-value", text: score.toFixed(0) }),
        ),
      ),
    ),
    report.words.length > 0
      ? el("div", { class: "card" },
          el("h4", { class: "card-title", text: "Words to work on" }),
          el("ul", { class: "word-list" }, ...report.words.map(wordRow)),
        )
      : null,
  );

  const tryAgain = el("button", { class: "primary wide", type: "button", text: "🔁 Try again" });
  tryAgain.addEventListener("click", actions.onBack);
  const toCatalog = el("button", { class: "secondary wide", type: "button", text: "« Catalog" });
  toCatalog.addEventListener("click", actions.onCatalog);

  view.append(
    el("div", { class: "report-actions" }, tryAgain, toCatalog),
  );

  target.replaceChildren(view);
}
