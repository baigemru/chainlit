import { execSync } from 'child_process';
import { defineConfig } from 'cypress';
import cypressSplit from 'cypress-split';
import fkill from 'fkill';
import { connect } from 'net';

import { runChainlit } from './cypress/support/run';

export const CHAINLIT_APP_PORT = 8000;

const isPortFree = () =>
  new Promise<boolean>((resolve) => {
    const socket = connect({ port: CHAINLIT_APP_PORT, host: '127.0.0.1' })
      .on('connect', () => {
        socket.destroy();
        resolve(false);
      })
      .on('error', () => resolve(true));
  });

const quietly = (command: string) => {
  try {
    execSync(command, { stdio: 'ignore' });
  } catch {
    // Nothing matched, or the tool is missing on this image.
  }
};

/**
 * A server left behind by the previous spec answers on the same port, so the
 * next spec silently tests the wrong app — which looks like the app never
 * rendered. fkill alone is not enough: it has been seen failing to resolve
 * the port owner on macOS, and lsof is absent from slim CI images, so try
 * every mechanism and then confirm the port actually came free.
 */
async function killChainlit() {
  await fkill(`:${CHAINLIT_APP_PORT}`, { force: true, silent: true });

  if (process.platform !== 'win32') {
    quietly(
      `lsof -ti tcp:${CHAINLIT_APP_PORT} -sTCP:LISTEN | xargs -r kill -9`
    );
    quietly(`fuser -k ${CHAINLIT_APP_PORT}/tcp`);
    quietly(`pkill -9 -f 'chainlit run'`);
  }

  for (let attempt = 0; attempt < 40; attempt++) {
    if (await isPortFree()) return;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }

  throw new Error(
    `Port ${CHAINLIT_APP_PORT} is still in use; the next spec would run against the previous app.`
  );
}

['SIGTERM', 'SIGINT', 'SIGHUP', 'SIGBREAK'].forEach((signal) => {
  process.on(signal, () => {
    (async () => {
      await killChainlit(); // Ensure Chainlit is killed on exit

      const signalMap = { SIGTERM: 15, SIGINT: 2, SIGHUP: 1, SIGBREAK: 21 };
      process.exit(128 + (signalMap[signal] || 0));
    })();
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
        await killChainlit();
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
        restartChainlit(spec: Cypress.Spec) {
          return new Promise((resolve) => {
            killChainlit().then(() => {
              runChainlit(spec).then(() => {
                setTimeout(() => {
                  resolve(null);
                }, 1000);
              });
            });
          });
        }
      });

      return config;
    }
  }
});
