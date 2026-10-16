import * as Dialog from "@radix-ui/react-dialog";
import { type ReactNode, useState } from "react";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  children?: ReactNode;
  confirmLabel: string;
  tone?: "primary" | "danger";
  /** The user must type this exact word to enable the confirm button. */
  requireText?: string;
  /** Show a required free-text reason field. */
  reasonLabel?: string;
  onConfirm: (reason: string) => Promise<unknown>;
  control: string; // data-control prefix, e.g. "killswitch.arm"
}

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  children,
  confirmLabel,
  tone = "primary",
  requireText,
  reasonLabel,
  onConfirm,
  control,
}: Props) {
  const [typed, setTyped] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ready = (!requireText || typed === requireText) && (!reasonLabel || reason.trim().length > 0) && !busy;

  const reset = (next: boolean) => {
    if (!next) {
      setTyped("");
      setReason("");
      setError(null);
    }
    onOpenChange(next);
  };

  const submit = async () => {
    if (!ready) return;
    setBusy(true);
    setError(null);
    try {
      await onConfirm(reason.trim());
      reset(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog.Root open={open} onOpenChange={reset}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="dialog" aria-describedby={undefined}>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void submit();
            }}
          >
            <div className="dialog-head">
              <Dialog.Title className="dialog-title">{title}</Dialog.Title>
            </div>
            <div className="dialog-body">
              {children}
              {reasonLabel && (
                <label className="field" style={{ flexDirection: "column", alignItems: "stretch" }}>
                  {reasonLabel}
                  <input
                    className="input"
                    autoFocus
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    data-control={`${control}.reason`}
                  />
                </label>
              )}
              {requireText && (
                <label className="field" style={{ flexDirection: "column", alignItems: "stretch" }}>
                  <span>
                    Type <strong className="num">{requireText}</strong> to confirm
                  </span>
                  <input
                    className="input"
                    autoFocus={!reasonLabel}
                    autoComplete="off"
                    value={typed}
                    onChange={(e) => setTyped(e.target.value)}
                    data-control={`${control}.text`}
                  />
                </label>
              )}
              {error && (
                <p className="form-error" role="alert">
                  {error}
                </p>
              )}
            </div>
            <div className="dialog-foot">
              <Dialog.Close asChild>
                <button type="button" className="btn" data-control={`${control}.cancel`}>
                  Cancel
                </button>
              </Dialog.Close>
              <button
                type="submit"
                className={tone === "danger" ? "btn btn-danger-solid" : "btn btn-primary"}
                disabled={!ready}
                data-control={`${control}.confirm`}
              >
                {busy ? "Working…" : confirmLabel}
              </button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
