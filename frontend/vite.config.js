import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Built output lands in the package FastAPI serves, and is committed. The
// demo must run from a clone with no node toolchain: a viva is not the place
// to discover that `npm install` is a prerequisite.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../src/enrolment/demo/static',
    emptyOutDir: true,
    assetsDir: 'assets',
  },
  server: {
    proxy: { '/api': 'http://127.0.0.1:8765' },
  },
})
