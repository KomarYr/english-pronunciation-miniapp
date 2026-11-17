/* Practice screen: catalog → exercise → record → report.

Kept deliberately simple: the catalog is one <details> per category (no
client-side router), the record flow is a small state machine inside the
exercise view, and the result renders through report.ts. */

import { assess, exercises as fetchExercises, addCustomText } from "../api";
import { ApiError } from "../api";
import { isAccessCode, renderAccessGate } from "./access";
import { Recorder } from "../recorder";
import { hideBackButton, showBackButton } from "../telegram";
import type { Exercise } from "../types";
import { el, formatSeconds, toast } from "../ui";
import { renderReport } from "./report";

const CATEGORY_LABELS: Record<string, string> = {
  minimal_pairs: "Minimal pairs",
  sentences: "Sentences",
  tongue_twisters: "Tongue twisters",
  paragraphs: "Paragraphs",
  custom: "Your texts",
};

/** Flow state of the exercise view, so stray callbacks can be abandoned. */
let active: {
  recorder: Recorder | null;
  abandoned: boolean;
  cleanup: () => void;
} | null = null;

function categoryLabel(category: string): string {
  return CATEGORY_LABELS[category] ?? category;
}

export async function renderPracticeList(target: HTMLElement): Promise<void> {
  abandonFlow();
  hideBackButton();
  target.replaceChildren(el("h2", { text: "Practice" }));

  const customCard = el("div", { class: "card custom-text-card" });
  const customForm = el("form", { class: "custom-text-form", hidden: true });
  const textarea = el("textarea", {
    rows: "4",
    placeholder: "Paste an English text (10–50 words)…",
  });
  const submit = el("button", { type: "submit", class: "primary" }, "Save & practice");
  customForm.append(textarea, submit);
  customForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    submit.disabled = true;
    try {
      const exercise = await addCustomText(textarea.value);
      customForm.hidden = true;
      textarea.value = "";
      void openExercise(target, exercise);
    } catch (error) {
      toast(error instanceof Error ? error.message : "Could not save the text", "error");
    } finally {
      submit.disabled = false;
    }
  });
  const toggle = el("button", { class: "secondary wide" }, "➕ Practice my own text");
  toggle.addEventListener("click", () => {
    customForm.hidden = !customForm.hidden;
    if (!customForm.hidden) textarea.focus();
  });
  customCard.append(toggle, customForm);
  target.append(customCard);

  let list: Exercise[] = [];
  try {
    list = (await fetchExercises()).exercises;
  } catch (error) {
    if (error instanceof ApiError && isAccessCode(error.code)) {
      renderAccessGate(target, error.code);
      return;
    }
    toast(error instanceof Error ? error.message : "Could not load exercises", "error");
    return;
  }

  const byCategory = new Map<string, Exercise[]>();
  for (const exercise of list) {
    const bucket = byCategory.get(exercise.category);
    if (bucket === undefined) byCategory.set(exercise.category, [exercise]);
    else bucket.push(exercise);
  }
  const listRoot = el("div", { class: "catalog" });
  for (const [category, items] of byCategory) {
    const details = el("details", { class: "category" });
    const summary = el("summary", {}, categoryLabel(category), el("span", { class: "count" }, `${items.length} exercise${items.length === 1 ? "" : "s"}`));
    details.append(summary);
    for (const exercise of items) {
      const row = el(
        "button",
        { class: "exercise-row", type: "button" },
        el("span", { class: "exercise-title" }, exercise.title),
        el("span", { class: "exercise-meta" }, exercise.level ?? "custom"),
      );
      row.addEventListener("click", () => void openExercise(target, exercise));
      details.append(row);
    }
    listRoot.append(details);
  }
  target.append(listRoot);
}

/** Show one exercise and run the record → assess flow. */
export async function openExercise(target: HTMLElement, exercise: Exercise): Promise<void> {
  abandonFlow();
  const flow = { recorder: null as Recorder | null, abandoned: false, cleanup: () => {} };
  active = flow;

  const back = el("button", { class: "back-link", type: "button", text: "‹ Catalog" });
  back.addEventListener("click", () => void renderPracticeList(target));

  const status = el("div", { class: "record-status" });
  const recordButton = el("button", { class: "record-button", type: "button" });

  const view = el(
    "section",
    { class: "exercise-view" },
    back,
    el("div", { class: "card" },
      el("h3", { class: "exercise-heading", text: exercise.title }),
      el("p", { class: "reference-text", text: exercise.reference_text }),
      exercise.phoneme_focus.length > 0
        ? el("div", { class: "chip-row" }, ...exercise.phoneme_focus.map((p) => el("span", { class: "chip", text: `/${p}/` })))
        : null,
    ),
    status,
    recordButton,
  );
  showBackButton(() => {
    if (!flow.abandoned) void renderPracticeList(target);
  });
  target.replaceChildren(view);

  const setState = (state: "idle" | "recording" | "assessing") => {
    view.dataset.recordState = state;
    // The flow is modal: hide the tab bar until it finishes or fails.
    document.body.classList.toggle("recording", state !== "idle");
    status.replaceChildren();
    recordButton.disabled = state !== "idle" && state !== "recording";
    if (state === "idle") {
      recordButton.textContent = "🎤 Start recording";
    } else if (state === "recording") {
      recordButton.textContent = "⏹ Stop";
      recordButton.disabled = false;
      status.append(
        el("span", { class: "pulse-dot", "aria-hidden": "true" }),
        el("span", { text: `Recording… up to ${formatSeconds(120)}` }),
      );
    } else {
      recordButton.textContent = "⏳ Assessing…";
    }
  };

  setState("idle");

  recordButton.addEventListener("click", async () => {
    if (flow.recorder !== null) {
      // While recording a second tap stops; while the recorder is still
      // spinning up it is ignored (the guard keeps out double starts).
      if (flow.recorder.recording) await flow.recorder.stop();
      return;
    }
    setState("recording");
    const recorder = new Recorder();
    flow.recorder = recorder;
    try {
      await recorder.start();
    } catch {
      setState("idle");
      toast("Microphone unavailable — allow access and try again.", "error");
      return;
    }
    if (flow.abandoned) return;
    const recording = await recorder.wait();
    if (flow.abandoned || recording === null) return;
    setState("assessing");
    try {
      const attempt = await assess(
        { exerciseId: exercise.id },
        recording.blob,
        recording.durationSeconds,
      );
      renderReport(target, attempt, {
        onBack: () => void openExercise(target, exercise),
        onCatalog: () => void renderPracticeList(target),
      });
    } catch (error) {
      setState("idle");
      if (error instanceof ApiError) {
        if (error.code === "no_speech") {
          toast("🤫 I didn't hear anything — read aloud right after starting.", "error");
        } else if (error.code === "quota_exceeded") {
          toast("🚫 Monthly audio budget is exhausted — resets on the 1st.", "error");
        } else {
          toast(error.message, "error");
        }
      } else {
        toast("Assessment failed — try again.", "error");
      }
    } finally {
      flow.recorder = null;
    }
  });

  flow.cleanup = () => {
    void flow.recorder?.stop();
  };
}

/** Called when the view is replaced: ignore in-flight callbacks. */
export function abandonFlow(): void {
  if (active !== null) {
    active.abandoned = true;
    active.cleanup();
    active = null;
  }
  document.body.classList.remove("recording");
}
