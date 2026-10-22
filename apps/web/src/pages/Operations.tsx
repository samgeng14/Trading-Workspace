import { Fragment, useState } from "react";
import { useDoctor, useEngineShadow, useExitTest, useRunDoctor, useRuns, useStatus } from "../api/hooks";
import { LoadError, PageHead, Panel, StatusBadge } from "../components/bits";
import { useKillSwitchDialogs } from "../components/KillSwitch";
import { NextJob, ScheduleList } from "../components/Schedule";
import { useToast } from "../components/Toasts";
import { dateShort, pct, shortTimeET, weekdayTimeET } from "../lib/format";
import { useServerNow } from "../lib/serverClock";

const DOCTOR_STATUS = { PASS: "pass", WARN: "warn", FAIL: "fail", SKIP: "skip" } as const;
const EXIT_STATUS = { PASS: "pass", FAIL: "fail", PENDING: "skip" } as const;
const EXIT_LABEL = { PASS: "Pass", FAIL: "Fail", PENDING: "Open" } as const;
const EXIT_CHECKS = [
  ["doctor", "Doctor"],
  ["run", "Run"],
  ["reconcile", "Reconcile"],
  ["nav", "NAV"],
] as const;
const SHADOW_KINDS = [
  ["reconcile", "Reconcile"],
  ["drift", "Gate"],
  ["whatif", "Basket now"],
  ["basket", "Basket sent"],
  ["orders", "Order states"],
] as const;

export function Operations() {
  return (
    <div className="page">
      <PageHead title="Operations" sub="System health, IBKR connection, schedule and run history" />
      <div className="grid">
        <HealthPanel />
        <KillSwitchPanel />
      </div>
      <DoctorPanel />
      <ExitTestPanel />
      <EngineShadowPanel />
      <div className="grid">
        <SchedulePanel />
        <RunsPanel />
      </div>
    </div>
  );
}

function HealthPanel() {
  const { data, error } = useStatus();
  return (
    <Panel
      title="System health"
      meta={data ? `as of ${shortTimeET(data.as_of)} ET` : undefined}
      className="span-7"
      flush
    >
      {error ? (
        <LoadError error={error} />
      ) : (
        <ul className="health-list">
          {data?.components.map((c) => (
            <li key={c.id}>
              <StatusBadge status={c.status} label={c.label} />
              <span className="secondary">{c.detail}</span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function KillSwitchPanel() {
  const ks = useStatus().data?.kill_switch;
  const killSwitch = useKillSwitchDialogs();
  return (
    <Panel title="Kill switch" className="span-5">
      {ks && (
        <div className="killswitch">
          {ks.armed ? (
            <>
              <StatusBadge status="error" label="Armed: trading halted" />
              <p className="secondary">
                {ks.reason ?? "No reason given"}
                {ks.changed_at ? ` · ${weekdayTimeET(ks.changed_at)} ET` : ""}
                {ks.changed_by ? ` by ${ks.changed_by}` : ""}
              </p>
              <button
                type="button"
                className="btn"
                onClick={killSwitch.openDisarm}
                data-control="ops.kill-switch.disarm"
              >
                Resume trading…
              </button>
            </>
          ) : (
            <>
              <StatusBadge status="ok" label="Clear: trading enabled" />
              <p className="secondary">
                Arming blocks every new order from the scheduler, the dashboard and the CLI until someone resumes, and
                cancels the orders still working at IBKR.
                {ks.changed_at
                  ? ` Last change ${dateShort(ks.changed_at.slice(0, 10))} by ${ks.changed_by ?? "unknown"}.`
                  : ""}
              </p>
              <button
                type="button"
                className="btn btn-danger"
                onClick={killSwitch.openArm}
                data-control="ops.kill-switch.arm"
              >
                Halt trading…
              </button>
            </>
          )}
        </div>
      )}
      {killSwitch.dialogs}
    </Panel>
  );
}

function DoctorPanel() {
  const { data, error } = useDoctor();
  const run = useRunDoctor();
  const toast = useToast();
  const counts = data?.results.reduce<Record<string, number>>(
    (acc, r) => ({ ...acc, [r.status]: (acc[r.status] ?? 0) + 1 }),
    {},
  );
  const summary = counts
    ? [
        counts.FAIL ? `${counts.FAIL} failed` : null,
        counts.WARN ? `${counts.WARN} warning${counts.WARN > 1 ? "s" : ""}` : null,
        `${counts.PASS ?? 0} passed`,
      ]
        .filter(Boolean)
        .join(", ")
    : "";
  // a report of nothing but SKIP is the API saying the doctor hasn't run here yet
  const notRun = !!counts && !counts.PASS && !counts.WARN && !counts.FAIL;
  return (
    <Panel
      title="IBKR doctor"
      meta={
        data
          ? notRun
            ? `not run yet on this machine · ${data.profile} on ${data.endpoint}`
            : `${summary} · ran ${weekdayTimeET(data.ran_at)} ET · ${data.profile} on ${data.endpoint}`
          : undefined
      }
      actions={
        <button
          type="button"
          className="btn"
          disabled={run.isPending}
          onClick={async () => {
            try {
              const report = await run.mutateAsync();
              const fails = report.results.filter((r) => r.status === "FAIL").length;
              toast(
                fails ? `Doctor: ${fails} check${fails > 1 ? "s" : ""} failed` : "Doctor: no failures",
                fails ? "error" : "success",
              );
            } catch (e) {
              toast(e instanceof Error ? e.message : String(e), "error");
            }
          }}
          data-control="ops.doctor.run"
        >
          {run.isPending ? "Running…" : "Run doctor"}
        </button>
      }
      flush
      foot={
        <>
          Same checks as <code>python -m src.broker.ibkr.doctor</code>. Order checks are whatIf previews; nothing is
          transmitted.
        </>
      }
    >
      {error ? (
        <LoadError error={error} />
      ) : (
        <div className="table-wrap">
          <table className="table doctor">
            <thead>
              <tr>
                <th>Status</th>
                <th>Check</th>
                <th>Result</th>
              </tr>
            </thead>
            <tbody>
              {data?.results.map((r) => (
                <tr key={r.name} data-status={r.status}>
                  <td>
                    <StatusBadge status={DOCTOR_STATUS[r.status]} label={r.status} />
                  </td>
                  <td className="sym">{r.name}</td>
                  <td className="doctor-detail">
                    <span>{r.detail}</span>
                    {r.fix && r.status !== "PASS" && <span className="doctor-fix">→ {r.fix}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function ExitTestPanel() {
  const { data, error } = useExitTest();
  const sessions = [...(data?.sessions ?? [])].reverse(); // newest first, as the runs
  return (
    <Panel
      title="Phase 0 exit test"
      meta={data ? `${data.streak} of ${data.needed} sessions in a row` : undefined}
      flush
      foot={
        <>
          Ten sessions in a row with the doctor green, the run ending ok, the reconcile matching IBKR to the share, and
          the close within {data ? pct(data.nav_tolerance) : "0.10%"} of IBKR&rsquo;s statement. Open: the evidence
          isn&rsquo;t in yet (today&rsquo;s, or a statement Flex publishes the next morning). Same as{" "}
          <code>bin/tradeit exit-test</code>.
        </>
      }
    >
      {error ? (
        <LoadError error={error} />
      ) : (
        <div className="table-wrap">
          <table className="table exit-test">
            <thead>
              <tr>
                <th>Session</th>
                {EXIT_CHECKS.map(([key, label]) => (
                  <th key={key}>{label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sessions.map((s) => {
                const why = EXIT_CHECKS.map(([key]) => s.checks[key])
                  .filter((c) => c.status === "FAIL" && c.note)
                  .map((c) => c.note);
                return (
                  <Fragment key={s.day}>
                    <tr data-status={s.status}>
                      <td className="num">{dateShort(s.day)}</td>
                      {EXIT_CHECKS.map(([key]) => (
                        <td key={key} title={s.checks[key].note || undefined}>
                          <StatusBadge
                            status={EXIT_STATUS[s.checks[key].status]}
                            label={EXIT_LABEL[s.checks[key].status]}
                          />
                        </td>
                      ))}
                    </tr>
                    {why.length > 0 && (
                      <tr className="exit-why" data-status={s.status}>
                        <td />
                        <td colSpan={EXIT_CHECKS.length}>{why.join(" · ")}</td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function EngineShadowPanel() {
  const { data, error } = useEngineShadow();
  const cosign = data?.role === "cosign";
  return (
    <Panel
      title={cosign ? "C++ engine, co-signing" : "C++ engine, in shadow"}
      meta={data ? `${data.streak} of ${data.needed} sessions in a row matching` : undefined}
      flush
      foot={
        <>
          Each run gives the C++ order manager the inputs the Python runner decided on: the reconcile of earlier orders,
          the rebalance gate, the basket it would build now, and on rebalance days the basket it sent, with the auction
          for each, and each order&rsquo;s statuses from IBKR, replayed. Match means the same breaks, the same decision
          and the same orders, to the share and the cent.{" "}
          {cosign ? (
            <>
              The engine co-signs (<code>config/engine.yaml</code>): a run sends no order unless the engine decided its
              reconcile, rebalance gate and basket the same.
              {data?.role_note ? ` ${data.role_note}, so it co-signs.` : ""}
            </>
          ) : (
            <>
              Python&rsquo;s orders go out either way. Once {data?.needed ?? 10} sessions in a row match, the engine can
              co-sign them, so that a difference blocks the run: <code>role: cosign</code> in{" "}
              <code>config/engine.yaml</code>, your call.
            </>
          )}{" "}
          Same as <code>bin/tradeit engine-shadow</code>.
        </>
      }
    >
      {error ? (
        <LoadError error={error} />
      ) : data && data.sessions.length === 0 ? (
        <p className="empty">No comparisons yet: the next daily run makes the first.</p>
      ) : (
        <div className="table-wrap">
          <table className="table engine-shadow">
            <thead>
              <tr>
                <th>Session</th>
                {SHADOW_KINDS.map(([key, label]) => (
                  <th key={key}>{label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(data?.sessions ?? []).map((s) => {
                const why = SHADOW_KINDS.flatMap(([key]) => {
                  const c = s.kinds[key];
                  return c && !c.match ? [c.summary] : [];
                });
                return (
                  <Fragment key={s.session}>
                    <tr data-status={s.match ? "match" : "differs"}>
                      <td className="num">{dateShort(s.session)}</td>
                      {SHADOW_KINDS.map(([key]) => {
                        const c = s.kinds[key];
                        return (
                          <td key={key} title={c?.summary}>
                            {c ? (
                              <StatusBadge status={c.match ? "pass" : "fail"} label={c.match ? "Match" : "Differs"} />
                            ) : (
                              <span className="muted">&mdash;</span>
                            )}
                          </td>
                        );
                      })}
                    </tr>
                    {why.length > 0 && (
                      <tr className="exit-why" data-status="differs">
                        <td />
                        <td colSpan={SHADOW_KINDS.length}>{why.join(" · ")}</td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function SchedulePanel() {
  const status = useStatus().data;
  const now = useServerNow();
  const next = status?.schedule.find((j) => j.status === "scheduled");
  return (
    <Panel title="Schedule" meta="exchange time (ET)" className="span-5" flush>
      <NextJob job={next} now={now} />
      <ScheduleList jobs={status?.schedule ?? []} />
    </Panel>
  );
}

function RunsPanel() {
  const { data, error } = useRuns();
  const [open, setOpen] = useState<string | null>(null);
  return (
    <Panel title="Recent runs" meta="daily runner, newest first" className="span-7" flush>
      {error ? (
        <LoadError error={error} />
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Started</th>
                <th>Result</th>
                <th>Summary</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data?.runs.map((r) => (
                <Fragment key={r.id}>
                  <tr>
                    <td className="num">{weekdayTimeET(r.started_at)}</td>
                    <td>
                      {r.ok ? (
                        <StatusBadge status="ok" label={r.dry_run ? "Dry run" : "OK"} />
                      ) : (
                        <StatusBadge status="error" label="Failed" />
                      )}
                    </td>
                    <td className="secondary">{r.summary}</td>
                    <td className="r">
                      <button
                        type="button"
                        className="btn btn-ghost"
                        aria-expanded={open === r.id}
                        onClick={() => setOpen(open === r.id ? null : r.id)}
                        data-control="ops.runs.details"
                      >
                        {open === r.id ? "Hide steps" : "Steps"}
                      </button>
                    </td>
                  </tr>
                  {open === r.id &&
                    r.steps.map((s) => (
                      <tr key={s.name} className="run-step">
                        <td />
                        <td>
                          {s.ok ? (
                            <StatusBadge status="ok" label="OK" />
                          ) : (
                            <StatusBadge status="error" label="Failed" />
                          )}
                        </td>
                        <td colSpan={2}>
                          <span className="sym">{s.name}</span> <span className="secondary">{s.detail}</span>
                        </td>
                      </tr>
                    ))}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
