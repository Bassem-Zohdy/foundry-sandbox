import { describe, expect, it } from "vitest";
import App from "./App.jsx";

describe("App", () => {
  it("renders the sandbox heading", () => {
    const element = App();
    expect(element.props.children).toBe("foundry sandbox");
  });
});
