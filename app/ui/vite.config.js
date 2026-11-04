import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Builds into app/web, which the Python server (afterglow/web.py) serves. The old report and night pages stay there.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../web", emptyOutDir: false, assetsDir: "app-assets" },
  server: { port: 5173, proxy: { "/api": process.env.AFTERGLOW_API || "http://127.0.0.1:8660", "/fonts": process.env.AFTERGLOW_API || "http://127.0.0.1:8660" } },
});
