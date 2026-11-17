/* App shell: boot, header, tab bar, screen switching. */

import { me, ApiError } from "./api";
import { isAccessCode, renderAccessGate } from "./screens/access";
import { abandonFlow, renderPracticeList } from "./screens/practice";
import { renderMistakes } from "./screens/mistakes";
import { renderStats } from "./screens/stats";
import { initTelegram } from "./telegram";
import { el, showScreen } from "./ui";
import type { Me } from "./types";

type Tab = "practice" | "mistakes" | "stats";

const appRoot = document.getElementById("app");
if (appRoot === null) throw new Error("#app is missing");

const content = el("main", { class: "content", id: "content" });
const tabBar = el(
  "nav",
  { class: "tab-bar" },
  ...(
    [
      ["practice", "🎤 Practice"],
      ["mistakes", "📕 Mistakes"],
      ["stats", "📊 Stats"],
    ] as const
  ).map(([tab, label]) =>
    el("button", { class: "tab", type: "button", "data-tab": tab, text: label }),
  ),
);
const header = el(
  "header",
  { class: "header" },
  el("div", {},
    el("h1", { text: "Pronunciation Trainer" }),
    el("p", { class: "muted", id: "user-line" }),
  ),
);
appRoot.append(header, content, tabBar);

function showTabError(target: HTMLElement, error: unknown): void {
  if (error instanceof ApiError && isAccessCode(error.code)) {
    renderAccessGate(target, error.code);
    return;
  }
  if (error instanceof ApiError && (error.code === "auth_required" || error.code === "auth_failed")) {
    target.replaceChildren(
      el("div", { class: "card center" },
        el("h2", { text: "Open from Telegram" }),
        el("p", { text: "This app authenticates through Telegram's initData." }),
        el("p", { class: "muted", text: "Open it via the bot's Menu button. For browser development set MINIAPP_DEV_MODE=1." }),
      ),
    );
    return;
  }
  target.replaceChildren(
    el("div", { class: "card center" },
      el("h2", { text: "Something went wrong" }),
      el("p", { class: "muted", text: error instanceof Error ? error.message : String(error) }),
    ),
  );
}

async function switchTab(tab: Tab): Promise<void> {
  abandonFlow();
  for (const button of tabBar.querySelectorAll<HTMLButtonElement>(".tab")) {
    button.classList.toggle("active", button.dataset.tab === tab);
  }
  try {
    if (tab === "practice") await renderPracticeList(content);
    else if (tab === "mistakes") await renderMistakes(content);
    else await renderStats(content);
  } catch (error) {
    showTabError(content, error);
  }
}

for (const button of tabBar.querySelectorAll<HTMLButtonElement>(".tab")) {
  button.addEventListener("click", () => void switchTab(button.dataset.tab as Tab));
}

async function boot(): Promise<void> {
  initTelegram();
  let info: Me;
  try {
    info = await me();
  } catch (error) {
    showScreen(content, el("div", {}));
    showTabError(content, error);
    return;
  }
  const userLine = document.getElementById("user-line");
  if (userLine !== null) {
    userLine.textContent = info.user.display_name + (info.dev_mode ? " · dev mode" : "");
  }
  document.body.classList.toggle("dev-mode", info.dev_mode);
  void switchTab("practice");
}

void boot();
