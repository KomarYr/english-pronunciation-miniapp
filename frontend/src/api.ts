/* Typed fetch wrapper for the backend JSON API.

Auth rides in the "Authorization: tma <initData>" header (Telegram's
standard); without Telegram the header is omitted and the backend must
be in MINIAPP_DEV_MODE to serve the request. Typed errors become
ApiError with the stable `code` field the backend guarantees. */

import { telegramInitData } from "./telegram";
import type { Attempt, Exercise, Me, Mistake, Quota, UserStats } from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public data: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  const initData = telegramInitData();
  if (initData !== null) headers.set("Authorization", `tma ${initData}`);
  if (
    options.body !== undefined &&
    options.body !== null &&
    !(options.body instanceof Blob) &&
    !headers.has("Content-Type")
  ) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as {
      detail?: { code?: string; message?: string } & Record<string, unknown>;
    } | null;
    const detail = payload?.detail ?? {};
    throw new ApiError(
      response.status,
      String(detail.code ?? "unknown"),
      String(detail.message ?? response.statusText),
      detail,
    );
  }
  return (await response.json()) as T;
}

export function me(): Promise<Me> {
  return request("/api/me");
}

export function exercises(): Promise<{ exercises: Exercise[] }> {
  return request("/api/exercises");
}

export async function addCustomText(text: string): Promise<Exercise> {
  const body = await request<{ exercise: Exercise }>("/api/texts", {
    method: "POST",
    body: JSON.stringify({ text }),
  });
  return body.exercise;
}

export function mistakes(): Promise<{ mistakes: Mistake[] }> {
  return request("/api/mistakes");
}

export function stats(): Promise<{ stats: UserStats; quota: Quota }> {
  return request("/api/stats");
}

/** Upload a browser recording; returns the graded attempt. */
export async function assess(
  target: { exerciseId: string } | { text: string },
  audio: Blob,
  durationSeconds: number,
): Promise<Attempt> {
  const params = new URLSearchParams(
    "exerciseId" in target
      ? { exercise_id: target.exerciseId }
      : { text: target.text },
  );
  const body = await request<{ attempt: Attempt }>(`/api/assess?${params}`, {
    method: "POST",
    body: audio,
    headers: {
      "Content-Type": "application/octet-stream",
      "X-Duration-Seconds": String(Math.round(durationSeconds)),
    },
  });
  return body.attempt;
}
