import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import App from "./App.jsx";

describe("App", () => {
  it("renders the app shell with the API status", () => {
    const html = renderToString(<App />);
    expect(html).toContain('data-testid="app-shell"');
    expect(html).toContain("API: <!-- -->checking");
  });
});
