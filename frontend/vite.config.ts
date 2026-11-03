import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  return {
    plugins: [react()],
    server: {
      port: Number(env.VITE_DEV_PORT || 5173),
      proxy: {
        // In development the UI calls /api and Vite forwards it to the FastAPI backend.
        "/api": { target: env.VITE_DEV_API_PROXY || "http://localhost:8000", changeOrigin: true },
      },
    },
    build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 1600 },
  };
});
