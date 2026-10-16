import { useState } from "react";
import { useSetKillSwitch } from "../api/hooks";
import { shortTimeET } from "../lib/format";
import { useServerNow } from "../lib/serverClock";
import { ConfirmDialog } from "./ConfirmDialog";
import { useToast } from "./Toasts";

/** Arm / disarm dialogs shared by the top bar, the halt banner and Operations. */
export function useKillSwitchDialogs() {
  const [arming, setArming] = useState(false);
  const [disarming, setDisarming] = useState(false);
  const setKill = useSetKillSwitch();
  const toast = useToast();
  const now = useServerNow();

  const dialogs = (
    <>
      <ConfirmDialog
        open={arming}
        onOpenChange={setArming}
        title="Halt all trading?"
        confirmLabel="Arm kill switch"
        tone="danger"
        reasonLabel="Reason (recorded in the audit log)"
        control="killswitch.arm"
        onConfirm={async (reason) => {
          await setKill.mutateAsync({ armed: true, reason });
          toast(`Kill switch armed at ${shortTimeET(now)} ET`, "error");
        }}
      >
        <p>
          New orders are blocked until someone resumes trading. Orders already working at the broker stay live; cancel
          them from Trading.
        </p>
      </ConfirmDialog>
      <ConfirmDialog
        open={disarming}
        onOpenChange={setDisarming}
        title="Resume trading?"
        confirmLabel="Disarm kill switch"
        requireText="RESUME"
        control="killswitch.disarm"
        onConfirm={async () => {
          await setKill.mutateAsync({ armed: false });
          toast("Kill switch disarmed. Trading resumes on the next scheduled run.", "success");
        }}
      >
        <p>Run the doctor and check the reconcile before resuming. The next scheduled run will submit orders again.</p>
      </ConfirmDialog>
    </>
  );

  return { openArm: () => setArming(true), openDisarm: () => setDisarming(true), dialogs };
}
