import { defineConfig } from '@playwright/test'

const apiPort = process.env.E2E_BACKEND_PORT ?? '8010'
const previewPort = process.env.E2E_PREVIEW_PORT ?? '4173'
const base = process.env.E2E_BASE_URL ?? 'postgresql+asyncpg://muse:muse@localhost:5434/postgres'
const oidcPort = process.env.E2E_OIDC_PORT ?? '9020'
const e2eDb = base.replace(/\/[^/?]*(\?.*)?$/, `/${process.env.E2E_DB_NAME ?? 'muse_e2e'}`)
const modelPort = process.env.E2E_MODEL_PORT ?? '9030'
const backendEnv = {
  MODEL_API_BASE_URL: `http://127.0.0.1:${modelPort}`,
  MODEL_API_KEY: 'fake-key-for-the-mock-model-server',
  DATABASE_URL: e2eDb,
  APP_ENV: 'local',
  SUPERADMIN_EMAILS: 'admin@example.com',
  SESSION_SECURE_COOKIE: 'false',
  SSE_PING_S: '5',
  OIDC_ISSUER: `http://127.0.0.1:${oidcPort}`,
  GOOGLE_OAUTH_CLIENT_ID: 'stub-client',
  GOOGLE_OAUTH_CLIENT_SECRET: 'stub-secret',
  OIDC_REDIRECT_URL: `http://127.0.0.1:${previewPort}/api/v1/auth/callback`,
}

export default defineConfig({
  testDir: './e2e',
  testMatch: /.*\.spec\.ts/,
  globalSetup: './e2e/global-setup.ts',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  expect: { timeout: 10_000 },
  use: { baseURL: `http://127.0.0.1:${previewPort}`, trace: 'off', locale: 'en-US', actionTimeout: 15_000 },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
  webServer: [
    {
      command: `uv run python frontend/e2e/tools/mock_model.py ${modelPort} tmp/mock-model.log`,
      cwd: '..',
      port: Number(modelPort),
      reuseExistingServer: false,
      timeout: 60000,
    },
    {
      command: `uv run python frontend/e2e/oidc_stub.py ${oidcPort}`,
      cwd: '..',
      url: `http://127.0.0.1:${oidcPort}/.well-known/openid-configuration`,
      reuseExistingServer: false,
      timeout: 60000,
    },
    {
      command: `uv run python -c "import app.tasks.worker, uvicorn; uvicorn.run('app.main:app', port=${apiPort})"`,
      cwd: '..',
      url: `http://127.0.0.1:${apiPort}/api/v1/health`,
      env: backendEnv,
      reuseExistingServer: false,
      timeout: 60000,
    },
    {
      // the worker's own output is the only trace of a hung or crashed job: keep it
      command: 'uv run procrastinate --app=app.tasks.worker.app worker 2>&1 | tee -a tmp/e2e-worker.log',
      cwd: '..',
      env: backendEnv,
      wait: { stdout: /Starting worker/ },
      reuseExistingServer: false,
      timeout: 60000,
    },
    {
      command: `npx vite preview --port ${previewPort} --strictPort --host 127.0.0.1`,
      env: { BACKEND_URL: `http://127.0.0.1:${apiPort}` },
      url: `http://127.0.0.1:${previewPort}/`,
      reuseExistingServer: false,
      timeout: 60000,
    },
  ],
})
