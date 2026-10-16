import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect } from "react";
import { BrowserRouter, Route, Routes } from "react-router";
import { applyFrame } from "./api/hooks";
import { startStream } from "./api/stream";
import { ToastProvider } from "./components/Toasts";
import { ServerClockProvider } from "./lib/serverClock";
import { Operations } from "./pages/Operations";
import { Overview } from "./pages/Overview";
import { Performance } from "./pages/Performance";
import { Positions } from "./pages/Positions";
import { Risk } from "./pages/Risk";
import { Trading } from "./pages/Trading";
import { KillBanner } from "./shell/KillBanner";
import { SideNav } from "./shell/SideNav";
import { TickerStrip } from "./shell/TickerStrip";
import { TopBar } from "./shell/TopBar";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: true, staleTime: 2_000 },
  },
});

export function App() {
  // one WebSocket for the whole dashboard; its frames land in the cache the hooks read
  useEffect(() => startStream((frame) => applyFrame(queryClient, frame)), []);

  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <ServerClockProvider>
          <BrowserRouter>
            <div className="app">
              <SideNav />
              <TopBar />
              <KillBanner />
              <main className="main">
                <Routes>
                  <Route path="/" element={<Overview />} />
                  <Route path="/positions" element={<Positions />} />
                  <Route path="/performance" element={<Performance />} />
                  <Route path="/risk" element={<Risk />} />
                  <Route path="/trading" element={<Trading />} />
                  <Route path="/ops" element={<Operations />} />
                </Routes>
              </main>
              <TickerStrip />
            </div>
          </BrowserRouter>
        </ServerClockProvider>
      </ToastProvider>
    </QueryClientProvider>
  );
}
