import type { Schemas } from "../api/client";
import { countdown, shortTimeET } from "../lib/format";
import { StatusBadge } from "./bits";

export function NextJob({ job, now }: { job: Schemas["ScheduledJob"] | undefined; now: Date }) {
  if (!job) return null;
  return (
    <div className="next-job">
      <span className="kicker">Next</span>
      <span>{job.label}</span>
      <span className="num next-job-time">
        {shortTimeET(job.at)} ET · in {countdown(Date.parse(job.at) - now.getTime())}
      </span>
    </div>
  );
}

export function ScheduleList({ jobs }: { jobs: Schemas["ScheduledJob"][] }) {
  return (
    <ol className="schedule">
      {jobs.map((j) => (
        <li key={j.id} className="schedule-row" data-status={j.status}>
          <span className="num schedule-time">{shortTimeET(j.at)}</span>
          <span className="schedule-label">{j.label}</span>
          <span className="schedule-status">
            {j.status === "done" && <StatusBadge status="ok" label={j.detail || "Done"} />}
            {j.status === "failed" && <StatusBadge status="error" label={j.detail || "Failed"} />}
            {j.status === "skipped" && <StatusBadge status="skip" label={j.detail || "Skipped"} />}
            {j.status === "running" && <StatusBadge status="warning" label="Running" />}
            {j.status === "scheduled" && <span className="muted">{j.detail || "Scheduled"}</span>}
          </span>
        </li>
      ))}
    </ol>
  );
}
