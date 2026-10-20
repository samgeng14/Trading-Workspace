import { keepPreviousData, type QueryClient, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Schemas, unwrap } from "./client";
import { type Frame, useStreamLive } from "./stream";

// Live views poll every few seconds. While the API's stream is up it pushes
// the streamed topics, and their polling drops to a safety net.
const LIVE_MS = 5_000;
const SAFETY_MS = 30_000;
const SLOW_MS = 60_000;

export const keys = {
  status: ["status"] as const,
  strip: ["market", "strip"] as const,
  summary: ["portfolio", "summary"] as const,
  positions: ["portfolio", "positions"] as const,
  intraday: ["portfolio", "intraday"] as const,
  performance: (range: string, benchmark: string) => ["performance", range, benchmark] as const,
  benchmarks: ["benchmarks"] as const,
  risk: ["risk"] as const,
  orders: ["trading", "orders"] as const,
  preview: ["trading", "preview"] as const,
  today: ["trading", "today"] as const,
  doctor: ["ops", "doctor"] as const,
  runs: ["ops", "runs"] as const,
};

/** Where each stream topic lands: the cache entry its hook below reads. */
export const streamKeys = {
  status: keys.status,
  strip: keys.strip,
  summary: keys.summary,
  positions: keys.positions,
  intraday: keys.intraday,
  orders: keys.orders,
} satisfies Record<Frame["topic"], readonly string[]>;

export function applyFrame(qc: QueryClient, frame: Frame): void {
  if (Object.hasOwn(streamKeys, frame.topic)) qc.setQueryData(streamKeys[frame.topic], frame.data);
}

const useStreamedInterval = () => (useStreamLive() ? SAFETY_MS : LIVE_MS);

export const useStatus = () =>
  useQuery({
    queryKey: keys.status,
    queryFn: () => unwrap(api.GET("/status")),
    refetchInterval: useStreamedInterval(),
  });

export const useMarketStrip = () =>
  useQuery({
    queryKey: keys.strip,
    queryFn: () => unwrap(api.GET("/market/strip")),
    refetchInterval: useStreamedInterval(),
  });

export const useSummary = () =>
  useQuery({
    queryKey: keys.summary,
    queryFn: () => unwrap(api.GET("/portfolio/summary")),
    refetchInterval: useStreamedInterval(),
  });

export const usePositions = () =>
  useQuery({
    queryKey: keys.positions,
    queryFn: () => unwrap(api.GET("/portfolio/positions")),
    refetchInterval: useStreamedInterval(),
  });

export const useIntraday = () =>
  useQuery({
    queryKey: keys.intraday,
    queryFn: () => unwrap(api.GET("/portfolio/intraday")),
    refetchInterval: useStreamedInterval(),
  });

export const useBenchmarks = () =>
  useQuery({ queryKey: keys.benchmarks, queryFn: () => unwrap(api.GET("/benchmarks")), staleTime: Infinity });

export const usePerformance = (range: Schemas["PerformanceRange"], benchmark: string) =>
  useQuery({
    queryKey: keys.performance(range, benchmark),
    queryFn: () => unwrap(api.GET("/performance", { params: { query: { range, benchmark } } })),
    placeholderData: keepPreviousData,
    refetchInterval: SLOW_MS,
  });

export const useRisk = () =>
  useQuery({ queryKey: keys.risk, queryFn: () => unwrap(api.GET("/risk")), refetchInterval: LIVE_MS * 2 });

export const useOrders = () =>
  useQuery({
    queryKey: keys.orders,
    queryFn: () => unwrap(api.GET("/trading/orders")),
    refetchInterval: useStreamedInterval(),
  });

export const useRebalancePreview = () =>
  useQuery({
    queryKey: keys.preview,
    queryFn: () => unwrap(api.GET("/trading/rebalance/preview")),
    refetchInterval: LIVE_MS * 2,
  });

/** Did the fund trade today; while a rebalance runs, its steps every second. */
export const useTradingDay = () =>
  useQuery({
    queryKey: keys.today,
    queryFn: () => unwrap(api.GET("/trading/today")),
    refetchInterval: (q) => (q.state.data?.state === "running" ? 1_000 : LIVE_MS * 2),
  });

export const useDoctor = () => useQuery({ queryKey: keys.doctor, queryFn: () => unwrap(api.GET("/ops/doctor")) });

export const useRuns = () =>
  useQuery({ queryKey: keys.runs, queryFn: () => unwrap(api.GET("/ops/runs")), refetchInterval: SLOW_MS });

// ── mutations ────────────────────────────────────────────────────────────

export function useSetKillSwitch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["KillSwitchRequest"]) => unwrap(api.POST("/ops/kill-switch", { body })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.status });
      void qc.invalidateQueries({ queryKey: keys.preview });
      void qc.invalidateQueries({ queryKey: keys.today });
    },
  });
}

export function useSubmitRebalance() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/trading/rebalance", { body: { confirm: "SUBMIT" } })),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: keys.orders });
      void qc.invalidateQueries({ queryKey: keys.preview });
      void qc.invalidateQueries({ queryKey: keys.today });
    },
  });
}

export function useCancelOrder() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (orderId: string) =>
      unwrap(api.POST("/trading/orders/{orderId}/cancel", { params: { path: { orderId } } })),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: keys.orders });
      void qc.invalidateQueries({ queryKey: keys.today });
    },
  });
}

export function useRunDoctor() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/ops/doctor/run")),
    onSuccess: (report) => qc.setQueryData(keys.doctor, report),
  });
}
