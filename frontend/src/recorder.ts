/* Browser microphone recorder: MediaRecorder wrapped small.

Produces a Blob (WebM/Opus where supported, MP4/AAC fallback on iOS)
plus the recorded duration, ready for the /api/assess upload. */

export interface Recording {
  blob: Blob;
  durationSeconds: number;
}

/** Auto-stop cap; the backend enforces the same guardrail. */
export const MAX_RECORD_SECONDS = 120;

const MIME_CANDIDATES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/mp4",
  "audio/ogg;codecs=opus",
];

function pickMime(): string {
  if (typeof MediaRecorder === "undefined") return "";
  for (const mime of MIME_CANDIDATES) {
    if (MediaRecorder.isTypeSupported(mime)) return mime;
  }
  return "";
}

export class Recorder {
  private recorder: MediaRecorder | null = null;
  private chunks: Blob[] = [];
  private timer: number | null = null;
  private startedAt = 0;
  private stopped: ((recording: Recording | null) => void) | null = null;

  get recording(): boolean {
    return this.recorder !== null && this.recorder.state === "recording";
  }

  /** Ask for the microphone and start recording. */
  async start(): Promise<void> {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    this.chunks = [];
    this.startedAt = performance.now();
    const mime = pickMime();
    this.recorder = mime
      ? new MediaRecorder(stream, { mimeType: mime })
      : new MediaRecorder(stream);
    this.recorder.addEventListener("dataavailable", (event: BlobEvent) => {
      if (event.data.size > 0) this.chunks.push(event.data);
    });
    this.recorder.addEventListener("stop", () => {
      stream.getTracks().forEach((track) => track.stop());
      this.clearTimer();
      const blob = new Blob(this.chunks, { type: this.recorder?.mimeType ?? mime });
      const durationSeconds = (performance.now() - this.startedAt) / 1000;
      const done = this.stopped;
      this.stopped = null;
      this.recorder = null;
      done?.(blob.size > 0 ? { blob, durationSeconds } : null);
    });
    this.recorder.start(1_000); // gather chunks every second
    this.timer = window.setTimeout(() => void this.stop(), MAX_RECORD_SECONDS * 1000);
    return new Promise((resolve, reject) => {
      if (this.recorder === null) {
        reject(new Error("recorder went away"));
        return;
      }
      this.recorder.addEventListener("start", () => resolve(), { once: true });
    });
  }

  /** Stop and deliver the recording to the promise returned by wait(). */
  async stop(): Promise<void> {
    if (this.recorder !== null && this.recorder.state !== "inactive") {
      this.recorder.stop();
    }
    this.clearTimer();
  }

  /** Resolves with the recording after stop(), or null when empty. */
  wait(): Promise<Recording | null> {
    return new Promise((resolve) => {
      this.stopped = resolve;
    });
  }

  private clearTimer(): void {
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }
}
