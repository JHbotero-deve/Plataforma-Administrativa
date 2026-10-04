import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ command }) => ({
  root: ".",
  plugins: [react()],
  base: command === "serve" ? "/" : "/headphones/",
  build: {
    outDir: "../frontend/headphones",
    emptyOutDir: true,
    assetsDir: "assets",
  },
}));
