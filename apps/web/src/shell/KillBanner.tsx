import { useStatus } from "../api/hooks";
import { useKillSwitchDialogs } from "../components/KillSwitch";
import { shortTimeET } from "../lib/format";

/** Full-width banner while the kill switch is armed. It can't be missed or dismissed. */
export function KillBanner() {
  const ks = useStatus().data?.kill_switch;
  const killSwitch = useKillSwitchDialogs();
  if (!ks?.armed) return null;
  return (
    <div className="kill-banner" role="alert">
      <strong>Trading halted</strong>
      <span>
        Kill switch armed{ks.changed_at ? ` at ${shortTimeET(ks.changed_at)} ET` : ""}
        {ks.changed_by ? ` by ${ks.changed_by}` : ""}
        {ks.reason ? `: ${ks.reason}` : ""}
      </span>
      <button
        type="button"
        className="btn kill-banner-btn"
        onClick={killSwitch.openDisarm}
        data-control="banner.resume"
      >
        Resume trading
      </button>
      {killSwitch.dialogs}
    </div>
  );
}
