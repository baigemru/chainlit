import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useEffect, useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import SchemaForm, { SchemaSection } from '@/components/SchemaForm';

/**
 * What a save carries besides the values: the page they were filled from.
 *
 * The engine stores the difference between the two, because the account
 * document has a second writer — the application, from a background arrival —
 * and a save of the whole document silently dropped whatever landed while the
 * page was open. So the form has to report what it was actually showing, and
 * that is the `values` it last reset from, not whatever the fetch layer is
 * holding at the moment Save is pressed. The second test is the one that
 * matters: it revalidates under an open form.
 */

vi.mock('@/components/i18n/Translator', () => ({
  Translator: ({ path }: { path: string }) => <span>{path}</span>,
  useTranslation: () => ({ t: (path: string) => path })
}));

const schema = {
  $ref: '#/$defs/Account',
  $defs: {
    Account: {
      type: 'object',
      title: 'Account',
      properties: { rate: { type: 'string', title: 'Rate' } }
    }
  }
};

const Harness = ({
  onSubmit,
  next
}: {
  onSubmit: (
    values: Record<string, unknown>,
    base: Record<string, unknown>
  ) => void;
  /** A page that arrives while the form is open, as a revalidation would. */
  next?: Record<string, unknown>;
}) => {
  const [values, setValues] = useState<Record<string, unknown>>({
    rate: '11.0'
  });
  useEffect(() => {
    if (next) setValues(next);
  }, [next]);
  return (
    <SchemaForm schema={schema} values={values} onSubmit={onSubmit}>
      <SchemaSection name="$leading" />
      <button type="submit">save</button>
    </SchemaForm>
  );
};

describe('a save from the account form', () => {
  it('carries the page it was filled from beside the edited values', async () => {
    const onSubmit = vi.fn();
    render(<Harness onSubmit={onSubmit} />);

    fireEvent.change(screen.getByDisplayValue('11.0'), {
      target: { value: '12.0' }
    });
    fireEvent.click(screen.getByText('save'));

    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    const [values, base] = onSubmit.mock.calls[0];
    expect(values).toMatchObject({ rate: '12.0' });
    expect(base).toMatchObject({ rate: '11.0' });
  });

  it('reports the page it adopted, not the one it was handed first', async () => {
    const onSubmit = vi.fn();
    const { rerender } = render(<Harness onSubmit={onSubmit} />);
    rerender(<Harness onSubmit={onSubmit} next={{ rate: '13.0' }} />);

    await waitFor(() => screen.getByDisplayValue('13.0'));
    fireEvent.click(screen.getByText('save'));

    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    expect(onSubmit.mock.calls[0][1]).toMatchObject({ rate: '13.0' });
  });
});
