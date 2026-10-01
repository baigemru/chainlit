import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

/**
 * The neutral surfaces stay neutral, in both themes.
 *
 * The palette is greys plus one accent, the primary. shadcn's slate template
 * left a blue tint in `--secondary` and in two foregrounds, which nobody saw
 * until a filled secondary button turned up pale blue among grey ones. A
 * token is read where it is written: jsdom applies no stylesheet.
 */
const HERE = dirname(fileURLToPath(import.meta.url));
const css = readFileSync(resolve(HERE, '../src/index.css'), 'utf8');

const block = (selector: string) =>
  css.split(`${selector} {`)[1]?.split('}')[0] ?? '';

const token = (selector: string, name: string) =>
  block(selector)
    .match(new RegExp(`--${name}:\\s*([^;]+);`))?.[1]
    .trim();

const NEUTRAL = [
  'secondary',
  'secondary-foreground',
  'muted',
  'muted-foreground',
  'accent',
  'accent-foreground',
  'border',
  'input'
];

describe('theme tokens', () => {
  it.each([':root', '.dark'])('keeps the greys grey in %s', (selector) => {
    for (const name of NEUTRAL) {
      const value = token(selector, name);
      expect(value, `--${name}`).toBeDefined();
      // HSL as "h s% l%": saturation zero or a rounding hair above it.
      const saturation = parseFloat(value!.split(/\s+/)[1]);
      expect(saturation, `--${name}: ${value}`).toBeLessThanOrEqual(1);
    }
  });

  it('keeps a filled secondary button readable', () => {
    // Lightness of fill against text, in both themes: neutralising the hue
    // must not have moved either end toward the other.
    const lightness = (selector: string, name: string) =>
      parseFloat(token(selector, name)!.split(/\s+/)[2]);
    for (const selector of [':root', '.dark']) {
      const gap = Math.abs(
        lightness(selector, 'secondary') -
          lightness(selector, 'secondary-foreground')
      );
      expect(gap).toBeGreaterThan(60);
    }
  });
});
