/// <reference types="vitest" />
import { defineConfig, mergeConfig } from 'vite';

import viteConfig from './vite.config';

/**
 * The app's own config, plus the test runner.
 *
 * Merged rather than restated so the dependency pins stay in one place.
 * They matter here for one spec: `elementSidebarFrame` drives the real
 * `useChatSession` and `useElementSidebar`, which live in
 * `libs/react-client` and resolve `react` from *their* `node_modules` — a
 * second copy whose hook dispatcher is null under this react-dom. The alias
 * in `vite.config.ts` is what collapses the two, and it exists there for the
 * same reason.
 *
 * That spec cannot simply move into `libs/react-client/tests/`, which is
 * where it belongs: that package has no `react-dom`, no
 * `@testing-library/react` and no react plugin in its vitest config, so it
 * would take three devDependencies and a lockfile change to a published
 * package to buy back these few lines.
 */
export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      environment: 'jsdom',
      setupFiles: './tests/setup-tests.ts',
      include: ['./**/*.{test,spec}.?(c|m)[jt]s?(x)']
    }
  })
);
