/* Access gate screens: what a pending or blocked caller sees instead of
   the app. Rendered wherever a request answers 403 with the `pending` or
   `blocked` code — the same contract the backend guarantees. */

import { el } from "../ui";

const SCREENS = {
  pending: {
    title: "Waiting for approval",
    body: "Your access request has been recorded — the bot owner decides who gets in.",
    hint: "Reopen the app once you are approved.",
  },
  blocked: {
    title: "Access blocked",
    body: "An admin has blocked your access to this trainer.",
    hint: "Your practice history is kept — contact the bot owner if you think this is a mistake.",
  },
} as const;

export type AccessCode = keyof typeof SCREENS;

export function isAccessCode(code: string): code is AccessCode {
  return code === "pending" || code === "blocked";
}

export function renderAccessGate(target: HTMLElement, code: AccessCode): void {
  const screen = SCREENS[code];
  target.append(
    el("div", { class: "card center" },
      el("h2", { text: screen.title }),
      el("p", { text: screen.body }),
      el("p", { class: "muted", text: screen.hint }),
    ),
  );
}
