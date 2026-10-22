import { useDatasets } from "../api/hooks";
import { LoadError, PageHead, Panel, StatusBadge } from "../components/bits";
import { dateShort, shortTimeET, weekdayTimeET } from "../lib/format";

// a dataset off on this machine (the store without Postgres) is not a fault
const STATE = { ok: "ok", behind: "warning", missing: "error", off: "unknown" } as const;
const LABEL = { ok: "Current", behind: "Behind", missing: "Missing", off: "Not kept here" } as const;

export function Data() {
  const { data, error } = useDatasets();
  const rows = data?.datasets ?? [];
  const current = rows.filter((d) => d.state === "ok" || d.state === "off").length;
  const last = data?.last_catchup;
  const failed = last?.actions.filter((a) => !a.ok).length ?? 0;
  return (
    <div className="page">
      <PageHead title="Data" sub="Every dataset's watermark against what should exist by now" />
      <Panel
        title="Datasets"
        meta={data ? `${current} of ${rows.length} current · as of ${shortTimeET(data.as_of)} ET` : undefined}
        flush
        foot={
          <>
            Each dataset should hold the session its job has filled by now, on the NYSE calendar.{" "}
            <code>bin/tradeit catchup</code> fills the ones behind that can be filled
            {last
              ? `; it last ran ${weekdayTimeET(last.at)} ET (${last.actions.length - failed} filled, ${failed} not)`
              : ""}
            .
          </>
        }
      >
        {error ? (
          <LoadError error={error} />
        ) : (
          <div className="table-wrap">
            <table className="table datasets">
              <thead>
                <tr>
                  <th>Dataset</th>
                  <th>Through</th>
                  <th>Expected</th>
                  <th className="r">Rows</th>
                  <th>Updated</th>
                  <th>State</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((d) => (
                  <tr key={d.name} data-state={d.state}>
                    <td className="dataset-name">
                      <span>{d.title}</span>
                      <span className="dataset-source">
                        {d.source} · {d.schedule}
                      </span>
                      {d.state !== "ok" && <span className="dataset-detail">{d.detail}</span>}
                      {d.findings.map((f) => (
                        <span key={f} className="dataset-finding">
                          {f}
                        </span>
                      ))}
                    </td>
                    <td className="num">{d.through ? dateShort(d.through) : "—"}</td>
                    <td className="num">{d.expected ? dateShort(d.expected) : "—"}</td>
                    <td className="num r">{d.rows === null ? "—" : d.rows.toLocaleString("en-US")}</td>
                    <td className="num">{d.written ? weekdayTimeET(d.written) : "—"}</td>
                    <td>
                      <StatusBadge status={STATE[d.state]} label={LABEL[d.state]} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
