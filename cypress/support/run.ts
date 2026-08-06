import {
  ChildProcessWithoutNullStreams,
  SpawnOptionsWithoutStdio,
  spawn
} from 'child_process';
import { access } from 'fs/promises';
import { dirname, join } from 'path';

let spawned: ChildProcessWithoutNullStreams | undefined;

const trackChainlit = (child: ChildProcessWithoutNullStreams) => {
  spawned = child;
  child.on('exit', () => {
    if (spawned === child) spawned = undefined;
  });
};

/**
 * Kills the server this harness started, process group included, and resolves
 * once it is really gone. Only ever touches our own child — never a chainlit
 * the developer is running themselves.
 */
export const stopChainlit = async (): Promise<void> => {
  const child = spawned;
  if (!child?.pid || child.exitCode !== null) return;

  const exited = new Promise<void>((resolve) =>
    child.once('exit', () => resolve())
  );
  const signal = (sig: NodeJS.Signals) => {
    try {
      process.kill(-child.pid!, sig);
    } catch {
      try {
        child.kill(sig);
      } catch {
        // Already gone.
      }
    }
  };

  signal('SIGTERM');
  const forced = setTimeout(() => signal('SIGKILL'), 3000);
  await exited;
  clearTimeout(forced);
};

export const runChainlit = async (
  spec: Cypress.Spec | null = null
): Promise<ChildProcessWithoutNullStreams> => {
  const CHAILIT_DIR = join(process.cwd(), 'backend', 'chainlit');
  const SAMPLE_DIR = join(CHAILIT_DIR, 'sample');

  return new Promise((resolve, reject) => {
    const testDir = spec ? dirname(spec.absolute) : SAMPLE_DIR;
    const entryPointFileName = spec
      ? spec.name.startsWith('async')
        ? 'main_async.py'
        : spec.name.startsWith('sync')
          ? 'main_sync.py'
          : 'main.py'
      : 'hello.py';

    const entryPointPath = join(testDir, entryPointFileName);

    if (!access(entryPointPath)) {
      return reject(
        new Error(`Entry point file does not exist: ${entryPointPath}`)
      );
    }

    const command = 'uv';

    const args = [
      '--project',
      CHAILIT_DIR,
      'run',
      'chainlit',
      'run',
      entryPointPath,
      '-h',
      '--ci'
    ];

    const options: SpawnOptionsWithoutStdio = {
      env: {
        ...process.env,
        CHAINLIT_APP_ROOT: testDir
      }
    };

    // Own process group: `uv run chainlit ...` spawns python as a grandchild,
    // so killing the direct child alone would orphan the server on the port.
    const chainlit = spawn(command, args, { ...options, detached: true });
    trackChainlit(chainlit);

    chainlit.stdout.on('data', (data) => {
      const output = data.toString();
      if (output.includes('Your app is available at')) {
        resolve(chainlit);
      }
    });

    chainlit.stderr.on('data', (data) => {
      console.error(`[Chainlit stderr] ${data}`);
    });

    chainlit.on('error', (error) => {
      reject(error.message);
    });

    chainlit.on('exit', function (code) {
      reject('Chainlit process exited with code ' + code);
    });
  });
};
