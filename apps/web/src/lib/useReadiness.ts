import { useMemo } from "react";
import { useDoctor, useRebalancePreview, useRisk, useRuns, useStatus } from "../api/hooks";
import { readiness } from "./readiness";

/** The shared readiness verdict; the queries are cached, so every caller reads the same data. */
export function useReadiness() {
  const status = useStatus().data;
  const preview = useRebalancePreview().data;
  const risk = useRisk().data;
  const doctor = useDoctor().data;
  const runs = useRuns().data;
  return useMemo(() => readiness({ status, preview, risk, doctor, runs }), [status, preview, risk, doctor, runs]);
}
