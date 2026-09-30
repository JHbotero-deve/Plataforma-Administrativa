import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  root: "product-analyzer/headphones",
  plugins: [react(), tailwindcss()],
  base: "/headphones/",
  build: {
    outDir: "../frontend/headphones",
    emptyOutDir: true,
    assetsDir: "assets",
  },
});
