import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

export type Schemas = components["schemas"];

export const api = createClient<paths>({ baseUrl: "/api/v1" });

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly problem: Schemas["Problem"] | undefined,
  ) {
    super(problem?.detail ? `${problem.title}: ${problem.detail}` : (problem?.title ?? `Request failed (${status})`));
  }
}

/** Resolve an openapi-fetch result to its data, or throw an ApiError. */
export async function unwrap<T>(call: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  const { data, error, response } = await call;
  if (error !== undefined || data === undefined) {
    throw new ApiError(response.status, error as Schemas["Problem"] | undefined);
  }
  return data;
}
