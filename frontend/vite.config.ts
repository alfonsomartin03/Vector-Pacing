/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The app is fully static: all calculations run in the browser (src/engine, in a Web Worker).
// VITE_BASE_PATH sets the public path, e.g. "/Vector-Pacing/" for GitHub Pages project sites.
export default defineConfig({
  plugins: [react()],
  base: process.env.VITE_BASE_PATH || '/',
  build: { chunkSizeWarningLimit: 800 }, // recharts dominates the main chunk
  worker: { format: 'es' }, // the worker lazy-loads the bundled drafting demo
  server: { port: 5173 },
  preview: { port: 4173 },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
  },
});
