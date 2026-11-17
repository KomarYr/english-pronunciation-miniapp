/* Mistake bank screen: exercises whose attempts average below 70/100.
   The backend derives it from attempt history — nothing to manage here. */

import { mistakes as fetchMistakes, ApiError } from "../api";
import { isAccessCode, renderAccessGate } from "./access";
import { openExercise } from "./practice";
import { el } from "../ui";
import type { Mistake } from "../types";

export async function renderMistakes(target: HTMLElement): Promise<void> {
  target.replaceChildren(el("h2", { text: "Mistake bank" }), el("p", { class: "muted", text: "Loading…" }));
  let items: Mistake[];
  try {
    items = (await fetchMistakes()).mistakes;
  } catch (error) {
    if (error instanceof ApiError && isAccessCode(error.code)) {
      renderAccessGate(target, error.code);
      return;
    }
    target.append(
      el("div", { class: "card center" },
        el("p", { text: "Could not load the mistake bank." }),
        el("p", { class: "muted", text: error instanceof Error ? error.message : "" }),
      ),
    );
    return;
  }

  if (items.length === 0) {
    target.append(
      el("div", { class: "card center" },
        el("p", { text: "🎉 Nothing weak right now." }),
        el("p", { class: "muted", text: "Exercises you read below 70/100 show up here automatically." }),
      ),
    );
    return;
  }

  const list = el("div", { class: "catalog" });
  for (const mistake of items) {
    const row = el(
      "button",
      { class: "exercise-row mistake-row", type: "button" },
      el("span", { class: "exercise-title" }, mistake.exercise.title),
      el("span", { class: "exercise-meta" },
        `avg ${Math.round(mistake.avg_pronunciation_score)} · ${mistake.attempt_count} attempt${mistake.attempt_count === 1 ? "" : "s"}`),
    );
    row.addEventListener("click", () => void openExercise(target, mistake.exercise));
    list.append(row);
  }
  target.append(list);
}
