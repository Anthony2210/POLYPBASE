import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    strictPort: true,
    allowedHosts: ['.trycloudflare.com'],
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/accounts': 'http://127.0.0.1:8000',
      '/boites': 'http://127.0.0.1:8000',
      // QR scan target: Django records the scan, then redirects to /boxes/<code>,
      // which this dev server serves as the React app.
      '/bac': 'http://127.0.0.1:8000',
    },
  },
});
