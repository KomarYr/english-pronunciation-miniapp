import { defineConfig } from "vite";

// base "./" keeps asset URLs relative, so the built app works no matter
// which path the web server mounts it under.
export default defineConfig({
  base: "./",
  build: { outDir: "dist", target: "es2022" },
});
