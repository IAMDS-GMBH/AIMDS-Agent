import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'node:path'

// Hermes Setup — Tauri-targeted Vite config.
//
// Port 5175 keeps us out of the way of:
//   web       (vite default 5173)
//   apps/desktop dev     (5174 per its package.json)
//
// `clearScreen: false` is the Tauri convention — they spawn vite as a child
// process and want our errors to stay visible.

const host = process.env.TAURI_DEV_HOST

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src')
    }
  },
  // Expose enterprise bundle defaults baked in at packaging time.
  // Set VITE_DEFAULT_BASE_URL in CI/packaging env to pre-fill the installer
  // credentials screen and promote Keycloak SSO as the primary login path.
  define: {
    'import.meta.env.VITE_DEFAULT_BASE_URL': JSON.stringify(process.env.VITE_DEFAULT_BASE_URL ?? ''),
    'import.meta.env.VITE_DEFAULT_KEYCLOAK_REALM': JSON.stringify(process.env.VITE_DEFAULT_KEYCLOAK_REALM ?? 'aimds'),
    'import.meta.env.VITE_DEFAULT_KEYCLOAK_REDIRECT_URI': JSON.stringify(process.env.VITE_DEFAULT_KEYCLOAK_REDIRECT_URI ?? ''),
  },
  clearScreen: false,
  server: {
    port: 5175,
    strictPort: true,
    host: host || '127.0.0.1',
    hmr: host
      ? {
          protocol: 'ws',
          host,
          port: 5176
        }
      : undefined,
    watch: {
      // Don't watch the Rust side — tauri-cli handles it.
      ignored: ['**/src-tauri/**']
    }
  },
  build: {
    target: 'esnext',
    outDir: 'dist',
    emptyOutDir: true
  }
})
