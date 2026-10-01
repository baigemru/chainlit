import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import postcss from 'postcss';
import tailwindcss from 'tailwindcss';

import config from '../tailwind.config';

/**
 * The font is the host's, set in its public/theme.json (`custom_fonts` plus
 * `--font-sans` / `--font-mono` under `variables`). Two things broke that:
 * `font-mono` compiled to Tailwind's own stack, so every label of a custom
 * element stayed in Menlo whatever the host chose; and the default Inter
 * shipped without 600, so `font-semibold` was a synthesized bold.
 */
const compile = async (cls: string) =>
  (
    await postcss([
      tailwindcss({ ...config, content: [{ raw: cls, extension: 'html' }] })
    ]).process('@tailwind utilities;', { from: undefined })
  ).css;

describe('theme fonts', () => {
  it('font-mono and font-sans read the theme variables', async () => {
    expect(await compile('font-mono')).toContain(
      'font-family: var(--font-mono)'
    );
    expect(await compile('font-sans')).toContain(
      'font-family: var(--font-sans)'
    );
  });

  it('the default Inter ships the semibold weight it is asked for', () => {
    const html = readFileSync(resolve(__dirname, '../index.html'), 'utf8');
    const block = html.slice(
      html.indexOf('<!-- FONT START -->'),
      html.indexOf('<!-- FONT END -->')
    );
    expect(block).toMatch(/Inter:wght@[0-9;]*600/);
  });
});
