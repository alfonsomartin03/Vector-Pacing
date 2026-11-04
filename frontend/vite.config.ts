/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const apiTarget = process.env.VITE_API_TARGET ?? 'http://127.0.0.1:8000';

export default defineConfig({
  plugins: [react()],
  base: process.env.VITE_BASE_PATH || '/',
  build: { chunkSizeWarningLimit: 800 }, // recharts dominates; acceptable for a local demo
  server: { port: 5173, proxy: { '/api': apiTarget } },
  preview: { port: 4173, proxy: { '/api': apiTarget } },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
  },
});
