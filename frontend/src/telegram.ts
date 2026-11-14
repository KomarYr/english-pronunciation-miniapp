/* Telegram bridge: init, initData capture, theme, back button.

SDK v3 exposes standalone signal-backed functions. Every call is
defensive: in a plain browser (dev mode) these throw or report as
"not available", and we degrade to browser behavior instead of crashing
so the backend can authenticate via MINIAPP_DEV_MODE. */

import {
  backButton,
  init,
  initData,
  miniApp,
  themeParams,
} from "@telegram-apps/sdk";

let initDataRaw: string | null = null;
let removeBackHandler: (() => void) | null = null;

/** Run a possibly-unavailable SDK call, swallowing platform errors. */
function attempt(action: () => unknown): void {
  try {
    action();
  } catch {
    /* plain browser or unsupported surface */
  }
}

/** Call once at boot. Safe in a plain browser. */
export function initTelegram(): void {
  attempt(init);
  attempt(() => void miniApp.mount());
  attempt(() => miniApp.bindCssVars());
  attempt(() => void miniApp.ready());
  attempt(() => themeParams.mount());
  attempt(() => themeParams.bindCssVars());
  attempt(() => backButton.mount());
  attempt(initData.restore);
  const raw = captureSignal(() => initData.raw());
  initDataRaw = typeof raw === "string" && raw.length > 0 ? raw : null;
}

function captureSignal<T>(signal: () => T): T | null {
  try {
    return signal();
  } catch {
    return null;
  }
}

/** The raw initData string, or null outside Telegram (dev mode). */
export function telegramInitData(): string | null {
  return initDataRaw;
}

/** Show the Telegram back button; `handler` runs on press. */
export function showBackButton(handler: () => void): void {
  removeBackHandler?.();
  removeBackHandler = null;
  attempt(() => {
    removeBackHandler = backButton.onClick(handler) ?? null;
  });
  attempt(() => backButton.show());
}

/** Hide the Telegram back button and drop the handler. */
export function hideBackButton(): void {
  attempt(() => backButton.hide());
  removeBackHandler?.();
  removeBackHandler = null;
}
