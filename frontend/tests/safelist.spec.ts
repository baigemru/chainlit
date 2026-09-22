import { beforeAll, describe, expect, it } from 'vitest';

import postcss from 'postcss';
import tailwindcss from 'tailwindcss';

import config from '../tailwind.config';

/**
 * The safelist is a contract with host applications.
 *
 * A custom element (`public/elements/*.jsx`) is compiled in the browser
 * against the stylesheet the frontend already shipped, so a utility it uses
 * exists only because this config kept it. Nothing in the app's own source
 * says so: the day a feature that happened to use `grid-cols-2` is deleted,
 * the class would vanish from the build and a host's card would silently
 * lose its grid.
 *
 * Tailwind is run here with an empty content set, so the only thing that can
 * put a class in the output is the safelist itself -- the compile is the
 * check, not a second description of the regexes.
 */
const EXPECTED = [
  // Layout and spacing, the original group.
  'grid-cols-2',
  'col-span-2',
  'gap-2',
  'p-4',
  'w-10',
  'h-10',
  'flex-col',
  'items-center',
  'justify-between',
  'truncate',
  'line-clamp-2',
  'overflow-x-auto',
  'rounded-full',
  'text-xs',
  'font-medium',
  // What a row with a thumbnail and a table of numbers needs.
  'flex',
  'grid',
  'hidden',
  'inline-flex',
  'block',
  'relative',
  'sticky',
  'top-0',
  'inset-0',
  'z-10',
  'divide-y',
  'divide-x',
  'object-cover',
  'object-contain',
  'tabular-nums',
  'line-through',
  'shrink-0',
  // The breakpoints a host element may design for.
  'sm:grid-cols-2',
  'md:grid-cols-3',
  'sm:flex-row',
  'md:flex-col',
  'sm:hidden',
  'md:hidden',
  'md:block',
  'sm:gap-4',
  'md:p-4',
  'md:w-full',
  'md:text-sm',
  'sm:items-center',
  'md:justify-between',
  // Colours and opacity, with the two variants they carry.
  'text-muted-foreground',
  'bg-primary',
  'border-accent',
  'opacity-50',
  'hover:bg-accent',
  'disabled:opacity-50'
];

/** The CSS selector for a class, with the characters CSS escapes. */
const selector = (name: string) => `.${name.replace(/[:/.]/g, '\\$&')}`;

let css = '';

beforeAll(async () => {
  const result = await postcss([
    tailwindcss({ ...config, content: [] })
  ]).process('@tailwind utilities;', { from: undefined });
  css = result.css;
}, 60_000);

describe('the custom-element safelist', () => {
  it('compiles nothing from an empty content set but the safelist', () => {
    // If this is ever false the spec is vacuous: every assertion below
    // would pass on whatever the app itself happened to use.
    expect(css).not.toBe('');
    expect(css).not.toContain(selector('animate-bounce-subtle'));
  });

  it.each(EXPECTED)('keeps %s', (name) => {
    expect(css).toContain(selector(name));
  });
});
