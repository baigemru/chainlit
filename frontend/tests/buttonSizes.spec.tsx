import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Button, type ButtonProps } from '@/components/ui/button';

/**
 * The text-sized buttons, `compact` (the mockup's `.b`) and `xs` (`.b-xs`).
 *
 * Read off the classes because that is all a size is. The cases that matter
 * are the ones that would go unnoticed: a new size that still carries
 * shadcn's fixed `h-9` box, and a fix for the faint outline that leaks into
 * the shadcn sizes host elements already draw with.
 */
const classes = (props: ButtonProps) => {
  const { container } = render(<Button {...props}>Go</Button>);
  return container.querySelector('button')!.className.split(/\s+/);
};

describe('button sizes', () => {
  it('sizes compact by its text, as the mockup does', () => {
    const c = classes({ size: 'compact' });

    expect(c).toEqual(
      expect.arrayContaining([
        'h-auto',
        'px-[11px]',
        'py-[5px]',
        'text-xs',
        'leading-4',
        'rounded-lg'
      ])
    );
    // shadcn's base carries `text-sm`; tailwind-merge must have dropped it.
    expect(c).not.toContain('text-sm');
    expect(c).not.toContain('h-9');
  });

  it('sizes xs by its text, a step smaller', () => {
    const c = classes({ size: 'xs' });

    expect(c).toEqual(
      expect.arrayContaining(['h-auto', 'px-2', 'py-[3px]', 'text-[11px]'])
    );
    expect(c).toContain('rounded-[7px]');
    expect(c).not.toContain('text-sm');
  });

  it('gives the outline a visible line at the text-sized steps only', () => {
    for (const size of ['compact', 'xs'] as const) {
      const c = classes({ size, variant: 'outline' });
      expect(c).toContain('border-muted-foreground/40');
      expect(c).not.toContain('border-input');
    }
    // shadcn's sizes keep shadcn's outline.
    expect(classes({ size: 'sm', variant: 'outline' })).toContain(
      'border-input'
    );
  });

  it('weights the accented button at the text-sized steps only', () => {
    expect(classes({ size: 'compact' })).toContain('font-semibold');
    expect(classes({ size: 'compact', variant: 'outline' })).toContain(
      'font-medium'
    );
    expect(classes({ size: 'sm' })).not.toContain('font-semibold');
  });

  it('leaves the shadcn sizes as they were', () => {
    expect(classes({ size: 'sm' })).toEqual(
      expect.arrayContaining(['h-9', 'px-3', 'text-sm'])
    );
    expect(classes({})).toEqual(expect.arrayContaining(['h-10', 'px-4']));
  });
});
