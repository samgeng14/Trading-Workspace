import { QueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { applyFrame, keys } from "./hooks";
import { type Frame, Stream, type StreamState, startStream, streamLive, streamState, streamUrl } from "./stream";

/** Stands in for the browser's WebSocket; the test opens, feeds and drops it. */
class FakeSocket {
  static made: FakeSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  onclose: (() => void) | null = null;
  closed = false;

  constructor(readonly url: string) {
    FakeSocket.made.push(this);
  }

  close() {
    this.closed = true;
  }

  open() {
    this.onopen?.();
  }

  receive(data: unknown) {
    this.onmessage?.({ data: typeof data === "string" ? data : JSON.stringify(data) });
  }

  drop() {
    this.onclose?.();
  }
}

const Socket = FakeSocket as unknown as typeof WebSocket;
const loc = { protocol: "http:", host: "localhost:5173" };
const last = () => FakeSocket.made[FakeSocket.made.length - 1]!;
const frame = (topic: string, data: unknown = {}) => ({ topic, at: "2026-10-09T18:12:00+00:00", data }) as Frame;

beforeEach(() => {
  FakeSocket.made = [];
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-09T18:12:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
});

describe("applyFrame", () => {
  it("lands each topic in the query its hook reads", () => {
    const qc = new QueryClient();
    const cases = [
      ["status", keys.status],
      ["strip", keys.strip],
      ["summary", keys.summary],
      ["positions", keys.positions],
      ["intraday", keys.intraday],
      ["orders", keys.orders],
    ] as const;
    for (const [topic, key] of cases) {
      const data = { from: topic };
      applyFrame(qc, frame(topic, data));
      expect(qc.getQueryData(key)).toBe(data);
    }
  });

  it("ignores a topic it doesn't know", () => {
    const qc = new QueryClient();
    applyFrame(qc, frame("weather", { rain: true }));
    expect(qc.getQueryCache().getAll()).toEqual([]);
  });
});

describe("Stream", () => {
  const make = () => {
    const frames: Frame[] = [];
    const states: StreamState[] = [];
    const stream = new Stream(
      "ws://x/api/v1/stream",
      (f) => frames.push(f),
      (s) => states.push(s),
      Socket,
    );
    return { stream, frames, states };
  };

  it("reconnects after 1, 2, 5 and then every 10 seconds", () => {
    const { stream } = make();
    stream.start();
    for (const wait of [1_000, 2_000, 5_000, 10_000, 10_000]) {
      const before = FakeSocket.made.length;
      last().drop();
      vi.advanceTimersByTime(wait - 1);
      expect(FakeSocket.made.length).toBe(before);
      vi.advanceTimersByTime(1);
      expect(FakeSocket.made.length).toBe(before + 1);
    }
    stream.stop();
  });

  it("starts the backoff over once frames arrive again", () => {
    const { stream } = make();
    stream.start();
    last().drop();
    vi.advanceTimersByTime(1_000);
    last().drop();
    vi.advanceTimersByTime(2_000);
    last().open();
    last().receive(frame("status"));
    last().drop();
    vi.advanceTimersByTime(1_000);
    expect(FakeSocket.made.length).toBe(4);
    stream.stop();
  });

  it("is live while the socket is open and records when the last frame came", () => {
    const { stream, frames, states } = make();
    stream.start();
    expect(states).toEqual([]);
    last().open();
    expect(states.at(-1)).toEqual({ live: true, lastFrameAt: null });
    vi.advanceTimersByTime(1_500);
    last().receive(frame("summary", { nav: 1 }));
    expect(frames).toEqual([frame("summary", { nav: 1 })]);
    expect(states.at(-1)).toEqual({ live: true, lastFrameAt: Date.parse("2026-10-09T18:12:01.500Z") });
    last().drop();
    expect(states.at(-1)?.live).toBe(false);
    stream.stop();
  });

  it("ignores frames that aren't JSON", () => {
    const { stream, frames } = make();
    stream.start();
    last().open();
    last().receive("not json");
    last().receive("null");
    expect(frames).toEqual([]);
    stream.stop();
  });

  it("stops for good: closes the socket and never reconnects", () => {
    const { stream, states } = make();
    stream.start();
    last().open();
    stream.stop();
    expect(last().closed).toBe(true);
    expect(states.at(-1)?.live).toBe(false);
    vi.advanceTimersByTime(60_000);
    expect(FakeSocket.made.length).toBe(1);
  });

  it("cancels a pending reconnect when stopped", () => {
    const { stream } = make();
    stream.start();
    last().drop();
    stream.stop();
    vi.advanceTimersByTime(60_000);
    expect(FakeSocket.made.length).toBe(1);
  });
});

describe("startStream", () => {
  it("never opens a socket in the demo build", () => {
    const stop = startStream(() => {}, { mode: "demo", Socket, loc });
    vi.advanceTimersByTime(60_000);
    expect(FakeSocket.made).toEqual([]);
    expect(streamState().live).toBe(false);
    stop();
  });

  it("opens one socket to this host's API and reports its state to the app", () => {
    const stop = startStream(() => {}, { mode: "production", Socket, loc });
    expect(FakeSocket.made.map((s) => s.url)).toEqual(["ws://localhost:5173/api/v1/stream"]);
    last().open();
    expect(streamState().live).toBe(true);
    stop();
    expect(streamState().live).toBe(false);
  });

  it("changes what the data hooks read only when the socket opens or drops, not per frame", () => {
    const stop = startStream(() => {}, { mode: "production", Socket, loc });
    const seen: boolean[] = [streamLive()];
    last().open();
    seen.push(streamLive());
    for (const topic of ["status", "strip", "summary", "intraday"]) {
      last().receive(frame(topic));
      seen.push(streamLive());
    }
    last().drop();
    seen.push(streamLive());
    // React re-renders a useSyncExternalStore reader only when its value changes
    expect(seen.filter((v, i) => i > 0 && !Object.is(v, seen[i - 1]))).toEqual([true, false]);
    expect(streamState().lastFrameAt).not.toBeNull();
    stop();
  });

  it("uses wss on an https page", () => {
    expect(streamUrl({ protocol: "https:", host: "fund.example" })).toBe("wss://fund.example/api/v1/stream");
  });
});
