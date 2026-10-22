import { createContext, type ReactNode, useContext, useEffect, useState } from "react";
import { useStatus } from "../api/hooks";

// The UI shows the backend's clock (exchange time), not the laptop's, so
// countdowns to the open/close and MOC cutoff agree with the server.
const ClockContext = createContext<Date>(new Date());

export function ServerClockProvider({ children }: { children: ReactNode }) {
  const asOf = useStatus().data?.as_of;
  const [offset, setOffset] = useState(0);
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    if (asOf) setOffset(Date.parse(asOf) - Date.now());
  }, [asOf]);

  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  return <ClockContext.Provider value={new Date(nowMs + offset)}>{children}</ClockContext.Provider>;
}

export const useServerNow = () => useContext(ClockContext);
