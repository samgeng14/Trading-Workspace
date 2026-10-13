import { ApiError, type Schemas } from "../api/client";

/** The API's 503 when the NAV history is too short to draw or measure anything. */
export function isShortHistory(error: unknown): boolean {
  return error instanceof ApiError && error.status === 503 && error.problem?.title === "Not enough NAV history yet";
}

/**
 * What an equity curve shows before IBKR's daily NAV is loaded: how much
 * history there is, why the curve can't be drawn yet, and the one command
 * that fills it in. Not an error: a new account or a fresh checkout starts here.
 */
export function HistoryEmpty({ history }: { history: Schemas["HistoryCoverage"] | undefined }) {
  const days = history?.days ?? 0;
  return (
    <div className="history-empty" role="status">
      <p className="kicker">No NAV history yet</p>
      <p className="history-empty-title">The equity curve starts once IBKR&rsquo;s daily NAV is loaded.</p>
      <p className="history-empty-body">
        {history && (
          <>
            The API has {days} {days === 1 ? "day" : "days"} of NAV so far
            {history.sources.length > 0 && <> ({history.sources.join(", ")})</>}.{" "}
          </>
        )}
        IBKR keeps your daily NAV since the account opened. One command on the Mac brings it in; from then on the API
        records every close itself.
      </p>
      <p className="history-empty-cmd">
        <code>bin/tradeit flex</code>
        <span>asks for the Flex Query ID and token, then draws the curve from inception</span>
      </p>
    </div>
  );
}
