import { useSyncExternalStore } from "react";
import type { Schemas } from "./client";

// One WebSocket for the whole dashboard: /api/v1/stream pushes each topic's
// REST body when it changes, and the hooks write it into the query cache. The
// demo build has no stream; it keeps polling the simulated backend.

export type Frame = Schemas["StreamMessage"];

/** Wait before each reconnect attempt; the last one repeats. */
export const BACKOFF_MS = [1_000, 2_000, 5_000, 10_000];

export interface StreamState {
  /** The socket is open. */
  live: boolean;
  /** Date.now() when the last frame arrived. */
  lastFrameAt: number | null;
}

function parse(data: unknown): Frame | null {
  if (typeof data !== "string") return null;
  try {
    const frame = JSON.parse(data) as Frame | null;
    return frame && typeof frame.topic === "string" ? frame : null;
  } catch {
    return null;
  }
}

/** A WebSocket that reconnects with backoff until stopped. */
export class Stream {
  private state: StreamState = { live: false, lastFrameAt: null };
  private socket: WebSocket | null = null;
  private retry: ReturnType<typeof setTimeout> | undefined;
  private attempt = 0;
  private stopped = false;

  constructor(
    private readonly url: string,
    private readonly onFrame: (frame: Frame) => void,
    private readonly onState: (state: StreamState) => void,
    private readonly Socket: typeof WebSocket = WebSocket,
  ) {}

  start(): void {
    const ws = new this.Socket(this.url);
    this.socket = ws;
    ws.onopen = () => this.set({ live: true });
    ws.onmessage = (ev) => {
      const frame = parse(ev.data);
      if (!frame) return;
      this.attempt = 0; // a stream that delivers is healthy again
      this.set({ lastFrameAt: Date.now() });
      this.onFrame(frame);
    };
    ws.onclose = () => {
      this.socket = null;
      if (this.state.live) this.set({ live: false });
      if (this.stopped) return;
      const delay = BACKOFF_MS[Math.min(this.attempt, BACKOFF_MS.length - 1)];
      this.attempt += 1;
      this.retry = setTimeout(() => this.start(), delay);
    };
  }

  stop(): void {
    this.stopped = true;
    clearTimeout(this.retry);
    const ws = this.socket;
    if (ws) {
      ws.onopen = ws.onmessage = ws.onclose = null;
      ws.close();
      this.socket = null;
    }
    if (this.state.live) this.set({ live: false });
  }

  private set(next: Partial<StreamState>): void {
    this.state = { ...this.state, ...next };
    this.onState(this.state);
  }
}

// ── the app's stream state, for the status strip and the polling intervals ──

let current: StreamState = { live: false, lastFrameAt: null };
const listeners = new Set<() => void>();

function publish(state: StreamState): void {
  current = state;
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export const streamState = (): StreamState => current;

export const streamLive = (): boolean => current.live;

/** The whole state, which changes with every frame: for the status strip. */
export const useStream = (): StreamState => useSyncExternalStore(subscribe, streamState);

/** Only whether the socket is open, so the data hooks don't re-render on every frame. */
export const useStreamLive = (): boolean => useSyncExternalStore(subscribe, streamLive);

export const streamUrl = (loc: Pick<Location, "protocol" | "host">): string =>
  `${loc.protocol === "https:" ? "wss" : "ws"}://${loc.host}/api/v1/stream`;

interface StartOptions {
  mode?: string;
  Socket?: typeof WebSocket;
  loc?: Pick<Location, "protocol" | "host">;
}

/** Opens the dashboard's stream, except in the demo build. Returns a stop function. */
export function startStream(
  onFrame: (frame: Frame) => void,
  { mode = import.meta.env.MODE, Socket = WebSocket, loc = location }: StartOptions = {},
): () => void {
  if (mode === "demo") return () => {};
  const stream = new Stream(streamUrl(loc), onFrame, publish, Socket);
  stream.start();
  return () => stream.stop();
}
