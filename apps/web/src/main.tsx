import "@fontsource-variable/newsreader/opsz.css";
import "@fontsource-variable/public-sans/wght.css";
import "@fontsource/ibm-plex-sans-condensed/400.css";
import "@fontsource/ibm-plex-sans-condensed/500.css";
import "@fontsource/ibm-plex-sans-condensed/600.css";
import "@fontsource/ibm-plex-sans-condensed/700.css";
import "./design/tokens.css";
import "./design/base.css";
import "./design/components.css";
import "./design/layout.css";
import "./design/charts.css";
import "./design/pages.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";

async function start() {
  // `vite --mode demo` serves the simulated backend from a service worker.
  if (import.meta.env.MODE === "demo") {
    const { worker } = await import("./mocks/browser");
    await worker.start({ onUnhandledRequest: "bypass", quiet: true });
  }
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}

void start();
