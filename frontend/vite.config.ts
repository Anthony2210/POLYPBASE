import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const apiProxyTarget = 'http://127.0.0.1:8000';

export default defineConfig(({ command }) => ({
  define: {
    __API_PROXY_ORIGIN__: JSON.stringify(command === 'serve' ? new URL(apiProxyTarget).origin : null),
  },
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    strictPort: true,
    allowedHosts: ['.trycloudflare.com'],
    proxy: {
      '/api': apiProxyTarget,
      '/accounts': apiProxyTarget,
      '/boites': apiProxyTarget,
      // Stable QR entry: Django hands the ID to React, which resolves it
      // through the scan API with the active organization header.
      '/bac': apiProxyTarget,
    },
  },
}));
