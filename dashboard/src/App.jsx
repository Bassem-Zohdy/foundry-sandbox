import { useEffect, useState } from "react";
import { fetchHealth } from "./health.js";
import { loadStatus } from "./status.js";

export default function App() {
  const [status, setStatus] = useState("checking");
  useEffect(() => {
    loadStatus(fetchHealth, setStatus);
  }, []);
  return (
    <main data-testid="app-shell">
      <h1>foundry sandbox</h1>
      <p>API: {status}</p>
    </main>
  );
}
