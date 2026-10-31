import { defineConfig } from '@playwright/test'

const base = process.env.E2E_BASE_URL ?? 'postgresql+asyncpg://muse:muse@localhost:5434/postgres'
const e2eDb = base.replace(/\/[^/?]*(\?.*)?$/, '/muse_e2e')
const backendEnv = {
  DATABASE_URL: e2eDb,
  APP_ENV: 'local',
  SUPERADMIN_EMAILS: 'admin@example.test',
  SESSION_SECURE_COOKIE: 'false',
  SSE_PING_S: '5',
}

export default defineConfig({
  testDir: './e2e',
  testMatch: /.*\.spec\.ts/,
  globalSetup: './e2e/global-setup.ts',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: { baseURL: 'http://127.0.0.1:4173', trace: 'off', locale: 'en-US' },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
  webServer: [
    {
      command: 'uv run uvicorn app.main:app --port 8010',
      cwd: '..',
      url: 'http://127.0.0.1:8010/api/v1/health',
      env: backendEnv,
      reuseExistingServer: false,
      timeout: 60000,
    },
    {
      command: 'uv run procrastinate --app=app.tasks.jobs.app worker',
      cwd: '..',
      env: backendEnv,
      wait: { stderr: /Starting worker/ },
      reuseExistingServer: false,
      timeout: 60000,
    },
    {
      command: 'npx vite preview --port 4173 --strictPort --host 127.0.0.1',
      url: 'http://127.0.0.1:4173/',
      reuseExistingServer: false,
      timeout: 60000,
    },
  ],
})
