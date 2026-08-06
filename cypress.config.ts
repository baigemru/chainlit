import { execSync } from 'child_process';
import { defineConfig } from 'cypress';
import cypressSplit from 'cypress-split';
import fkill from 'fkill';
import { connect } from 'net';

import { runChainlit, stopChainlit } from './cypress/support/run';

export const CHAINLIT_APP_PORT = 8000;

const isPortFree = () =>
  new Promise<boolean>((resolve) => {
    const socket = connect({ port: CHAINLIT_APP_PORT, host: '127.0.0.1' });
    const done = (free: boolean) => {
      socket.destroy();
      resolve(free);
    };
    socket.setTimeout(1000);
    socket.on('connect', () => done(false));
    socket.on('error', () => done(true));
    // Neither accepted nor refused: treat as busy rather than hanging here.
    socket.on('timeout', () => done(false));
  });

/**
 * A server left behind by the previous spec answers on the same port, so the
 * next spec silently tests the wrong app — which looks like the app never
 * rendered. Stop our own child first (process group included, since `uv run`
 * puts the server in a grandchild), then fall back to whoever owns the port.
 *
 * Deliberately never matches processes by name: a developer running their own
 * `chainlit run` must not be killed by the test suite.
 */
async function killChainlit({ strict = false } = {}) {
  await stopChainlit();

  await fkill(`:${CHAINLIT_APP_PORT}`, { force: true, silent: true });

  if (process.platform !== 'win32') {
    try {
      execSync(
        `lsof -ti tcp:${CHAINLIT_APP_PORT} -sTCP:LISTEN | xargs kill -9`,
        { stdio: 'ignore' }
      );
    } catch {
      // No listener, or lsof is missing on this image.
    }
  }

  for (let attempt = 0; attempt < 40; attempt++) {
    if (await isPortFree()) return;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }

  const message = `Port ${CHAINLIT_APP_PORT} is still in use; the next spec would run against the previous app.`;
  // Only worth failing the run before a spec starts — after one, or on the
  // way out, throwing would just bury the results.
  if (strict) throw new Error(message);
  console.warn(message);
}

['SIGTERM', 'SIGINT', 'SIGHUP', 'SIGBREAK'].forEach((signal) => {
  process.on(signal, () => {
    const signalMap = { SIGTERM: 15, SIGINT: 2, SIGHUP: 1, SIGBREAK: 21 };
    killChainlit()
      .catch(() => undefined)
      .finally(() => process.exit(128 + (signalMap[signal] || 0)));
  });
});

export default defineConfig({
  projectId: 'ij1tyk',

  retries: 3,

  viewportWidth: 1200,

  e2e: {
    defaultCommandTimeout: 30000,
    baseUrl: `http://127.0.0.1:${CHAINLIT_APP_PORT}`,
    experimentalInteractiveRunEvents: true,
    async setupNodeEvents(on, config) {
      cypressSplit(on, config);

      await killChainlit(); // Fallback to ensure no previous instance is running
      await runChainlit(); // Start Chainlit before running tests as Cypress require

      on('before:spec', async (spec) => {
        await killChainlit({ strict: true });
        await runChainlit(spec);
      });

      on('after:spec', async () => {
        await killChainlit();
      });

      on('after:run', async () => {
        await killChainlit();
      });

      on('task', {
        log(message) {
          console.log(message);
          return null;
        },
        async restartChainlit(spec: Cypress.Spec) {
          // Must settle even on failure, otherwise the calling task hangs
          // until its timeout with no explanation.
          try {
            await killChainlit();
            await runChainlit(spec);
            await new Promise((resolve) => setTimeout(resolve, 1000));
          } catch (error) {
            console.error(`Failed to restart Chainlit: ${error}`);
          }
          return null;
        }
      });

      return config;
    }
  }
});
