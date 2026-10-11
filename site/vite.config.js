import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base "./" so the same build works at the Vercel root and under /GolfIn3D/ on GitHub Pages
export default defineConfig({ base: "./", plugins: [react()] });
