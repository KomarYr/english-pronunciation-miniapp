/* Tiny DOM helpers: the whole "framework" of this vanilla app. */

/** Create an element with attributes and children in one call. */
export function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  attrs: Record<string, string | number | boolean> = {},
  ...children: (Node | string | null | undefined)[]
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = String(value);
    else if (key === "text") node.textContent = String(value);
    else if (value === false) continue;
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children) {
    if (child === null || child === undefined) continue;
    node.append(child instanceof Node ? child : document.createTextNode(child));
  }
  return node;
}

/** Clear a container and show one screen element inside it. */
export function showScreen(root: HTMLElement, screen: HTMLElement): void {
  root.replaceChildren(screen);
}

/** Replace the children of an element. */
export function setChildren(node: HTMLElement, ...children: (Node | string)[]): void {
  node.replaceChildren(...children);
}

let toastTimer: number | undefined;

/** Transient message at the top of the screen. */
export function toast(message: string, kind: "info" | "error" = "info"): void {
  let node = document.querySelector<HTMLElement>(".toast");
  if (node === null) {
    node = el("div", { class: "toast", role: "status" });
    document.body.append(node);
  }
  node.textContent = message;
  node.className = `toast ${kind} visible`;
  clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => node.classList.remove("visible"), 3200);
}

/** Format seconds as a friendly duration ("2m 30s"). */
export function formatSeconds(seconds: number): string {
  const whole = Math.round(seconds);
  if (whole < 60) return `${whole}s`;
  return `${Math.floor(whole / 60)}m ${whole % 60}s`;
}
