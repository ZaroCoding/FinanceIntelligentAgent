import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
  // Recharts is large; the single bundle is fine for a local classroom demo.
  build: { chunkSizeWarningLimit: 1000 },
});
