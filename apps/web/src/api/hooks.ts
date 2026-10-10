import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Schemas, unwrap } from "./client";

// Live views poll every few seconds; the engine's WebSocket feed replaces
// this in Phase 3 without changing the hooks' callers.
const LIVE_MS = 5_000;
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
  doctor: ["ops", "doctor"] as const,
  runs: ["ops", "runs"] as const,
};

export const useStatus = () =>
  useQuery({ queryKey: keys.status, queryFn: () => unwrap(api.GET("/status")), refetchInterval: LIVE_MS });

export const useMarketStrip = () =>
  useQuery({ queryKey: keys.strip, queryFn: () => unwrap(api.GET("/market/strip")), refetchInterval: LIVE_MS });

export const useSummary = () =>
  useQuery({ queryKey: keys.summary, queryFn: () => unwrap(api.GET("/portfolio/summary")), refetchInterval: LIVE_MS });

export const usePositions = () =>
  useQuery({
    queryKey: keys.positions,
    queryFn: () => unwrap(api.GET("/portfolio/positions")),
    refetchInterval: LIVE_MS,
  });

export const useIntraday = () =>
  useQuery({
    queryKey: keys.intraday,
    queryFn: () => unwrap(api.GET("/portfolio/intraday")),
    refetchInterval: LIVE_MS,
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
  useQuery({ queryKey: keys.orders, queryFn: () => unwrap(api.GET("/trading/orders")), refetchInterval: LIVE_MS });

export const useRebalancePreview = () =>
  useQuery({
    queryKey: keys.preview,
    queryFn: () => unwrap(api.GET("/trading/rebalance/preview")),
    refetchInterval: LIVE_MS * 2,
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
    },
  });
}

export function useCancelOrder() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (orderId: string) =>
      unwrap(api.POST("/trading/orders/{orderId}/cancel", { params: { path: { orderId } } })),
    onSettled: () => void qc.invalidateQueries({ queryKey: keys.orders }),
  });
}

export function useRunDoctor() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/ops/doctor/run")),
    onSuccess: (report) => qc.setQueryData(keys.doctor, report),
  });
}
