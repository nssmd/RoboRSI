import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Dev: proxy /api → the local session-cockpit webapi (scripts/session_cockpit.py,
// default :8795). Prod: `vite build` emits static assets the API serves from
// frontend/web/dist on the same port.
const API = process.env.ROBORSI_WEB_API ?? 'http://127.0.0.1:8795';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: API, changeOrigin: true, ws: true },
    },
  },
  build: { outDir: 'dist', sourcemap: false },
});
