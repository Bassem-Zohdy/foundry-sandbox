import { useEffect, useState } from "react";
import { fetchHealth } from "./health.js";

export default function App() {
  const [status, setStatus] = useState("checking");
  useEffect(() => {
    fetchHealth()
      .then((body) => setStatus(body.status))
      .catch(() => setStatus("unavailable"));
  }, []);
  return (
    <main data-testid="app-shell">
      <h1>foundry sandbox</h1>
      <p>API: {status}</p>
    </main>
  );
}
