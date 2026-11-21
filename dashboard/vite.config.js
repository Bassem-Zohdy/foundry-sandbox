import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: { modulePreload: { polyfill: false } },
  test: {
    include: ["src/**/*.{test,spec}.{js,jsx,ts,tsx}"],
    coverage: { include: ["src/**/*.{js,jsx,ts,tsx}"], exclude: ["src/main.jsx", "src/**/*.{test,spec}.*"] },
  },
});
