/**
 * A custom element is a piece of the host's UI with its own state in it: a
 * typed filter, an opened accordion, a half-filled form. Everything below
 * asserts the same thing from a different side -- that state belongs to the
 * element and is destroyed only by the element being replaced.
 *
 * The compiled source is real: `react-runner` runs sucrase in jsdom just as it
 * does in a browser, so a test that says "still mounted" is looking at the same
 * component instance the user would be.
 */
import { act, fireEvent, render, screen } from '@testing-library/react';
import { MessageContext, defaultMessageContext } from 'contexts/MessageContext';
import { type ReactNode } from 'react';
import { RecoilRoot, useSetRecoilState } from 'recoil';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  type ChainlitAPI,
  ChainlitContext,
  type IAsk,
  type ICustomElement,
  askUserState,
  sessionIdState
} from '@chainlit/react-client';

import CustomElement from '@/components/Elements/CustomElement';
import { resetElementSourceCache } from '@/components/Elements/CustomElement/source';

// No block comment directly above this call. Bisected on vitest 0.34: with one
// there, the mock is not hoisted above the imports, the real module loads, and
// every case dies on "Missing definition for RecoilValue" rather than saying
// what it meant. Line comments like these are fine.
vi.mock('@chainlit/react-client', async () => {
  // Atoms made here, as `parentThreadFlag.spec.tsx` does: the built package and
  // this spec hold two Recoil module instances, and an atom created in one is
  // unknown to the other. `useAuth` and `useChatInteract` say nothing about
  // whether an element stays mounted; `askUserState` and the client, which the
  // cases do drive, are real.
  const { atom } = await import('recoil');
  const { createContext } = await import('react');
  return {
    sessionIdState: atom<string | undefined>({
      key: 'test/SessionId',
      default: 'sess-1'
    }),
    askUserState: atom<unknown>({ key: 'test/AskUser', default: undefined }),
    ChainlitContext: createContext(undefined),
    useAuth: () => ({ user: { identifier: 'alice' } }),
    useChatInteract: () => ({ sendMessage: () => undefined })
  };
});

const COUNTER_SOURCE = `
function Counter() {
  const [n, setN] = React.useState(0);
  return (
    <div>
      <span data-testid="local">{n}</span>
      <span data-testid="label">{String(props.label)}</span>
      <button data-testid="inc" onClick={() => setN(n + 1)}>inc</button>
    </div>
  );
}
`;

// The scope's callbacks, reached the way a host element reaches them: as bare
// identifiers the compiled function closed over at compile time.
const CALLER_SOURCE = `
function Caller() {
  return (
    <div>
      <button data-testid="update" onClick={() => updateElement({ label: 'z' })}>u</button>
      <button data-testid="submit" onClick={() => submitElement({ picked: 1 })}>s</button>
    </div>
  );
}
`;

// The one shape that can catch a stale handler ref: React flushes passive
// effects child-first, so this effect runs before anything the host component
// does after the same commit.
const AUTOSUBMIT_SOURCE = `
function AutoSubmit() {
  React.useEffect(() => {
    submitElement({ auto: true });
  });
  return <span data-testid="auto">ready</span>;
}
`;

const THROWER_SOURCE = `
function Thrower() {
  throw new Error('boom');
}
`;

const SOURCES: Record<string, string> = {
  Counter: COUNTER_SOURCE,
  Caller: CALLER_SOURCE,
  AutoSubmit: AUTOSUBMIT_SOURCE,
  Thrower: THROWER_SOURCE
};

const fetchSource = vi.fn();

const updateElement = vi.fn(async () => ({}));

const apiClient = {
  httpEndpoint: 'http://test',
  get: (endpoint: string) => fetchSource(endpoint),
  updateElement,
  deleteElement: vi.fn(async () => ({})),
  callAction: vi.fn(async () => ({}))
} as unknown as ChainlitAPI;

const makeElement = (
  overrides: Partial<ICustomElement> = {}
): ICustomElement => ({
  id: 'el-1',
  type: 'custom',
  name: 'Counter',
  display: 'inline',
  forId: 'step-1',
  props: { label: 'a' },
  ...overrides
});

let setAsk: (ask: IAsk | undefined) => void;

const AskBridge = () => {
  setAsk = useSetRecoilState(askUserState) as (ask: IAsk | undefined) => void;
  return null;
};

const Harness = ({
  children,
  ctx,
  hidden
}: {
  children: ReactNode;
  ctx?: Partial<typeof defaultMessageContext>;
  hidden?: boolean;
}) => (
  <RecoilRoot initializeState={({ set }) => set(sessionIdState, 'sess-1')}>
    <AskBridge />
    <ChainlitContext.Provider value={apiClient as never}>
      <MessageContext.Provider value={{ ...defaultMessageContext, ...ctx }}>
        <div hidden={hidden}>{children}</div>
      </MessageContext.Provider>
    </ChainlitContext.Provider>
  </RecoilRoot>
);

const local = () => screen.getByTestId('local').textContent;
const label = () => screen.getByTestId('label').textContent;

/** Give the source fetch its microtask, then let React commit. */
const settle = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
};

beforeEach(() => {
  resetElementSourceCache();
  fetchSource.mockReset();
  updateElement.mockClear();
  fetchSource.mockImplementation(async (endpoint: string) => {
    const name = endpoint.replace('/public/elements/', '').replace('.jsx', '');
    return { text: async () => SOURCES[name] };
  });
});

describe('custom element stability', () => {
  it('keeps its state when the message context is rebuilt', async () => {
    const element = makeElement();
    const { rerender } = render(
      <Harness ctx={{ loading: false }}>
        <CustomElement element={element} />
      </Harness>
    );
    await settle();

    fireEvent.click(screen.getByTestId('inc'));
    expect(local()).toBe('1');

    // What `MessagesContainer` does at the start of every run.
    rerender(
      <Harness ctx={{ loading: true }}>
        <CustomElement element={element} />
      </Harness>
    );

    expect(local()).toBe('1');
  });

  it('keeps its state while an ask opens and closes', async () => {
    const element = makeElement();
    render(
      <Harness>
        <CustomElement element={element} />
      </Harness>
    );
    await settle();

    fireEvent.click(screen.getByTestId('inc'));
    fireEvent.click(screen.getByTestId('inc'));
    expect(local()).toBe('2');

    const ask: IAsk = {
      callback: vi.fn(),
      spec: { type: 'element', stepId: 'step-1', timeout: 30 }
    };
    act(() => setAsk(ask));
    expect(local()).toBe('2');

    act(() => setAsk(undefined));
    expect(local()).toBe('2');
  });

  it('sees a props update in render and keeps its state', async () => {
    const element = makeElement();
    const { rerender } = render(
      <Harness>
        <CustomElement element={element} />
      </Harness>
    );
    await settle();

    fireEvent.click(screen.getByTestId('inc'));
    expect(local()).toBe('1');
    expect(label()).toBe('a');

    // `element.update` from the server: a new element object, same id.
    rerender(
      <Harness>
        <CustomElement element={makeElement({ props: { label: 'b' } })} />
      </Harness>
    );

    expect(label()).toBe('b');
    expect(local()).toBe('1');
  });

  it('keeps its state when a parent hides and shows it', async () => {
    const element = makeElement();
    const { rerender } = render(
      <Harness hidden={false}>
        <CustomElement element={element} />
      </Harness>
    );
    await settle();

    fireEvent.click(screen.getByTestId('inc'));
    expect(local()).toBe('1');

    rerender(
      <Harness hidden>
        <CustomElement element={element} />
      </Harness>
    );
    expect(local()).toBe('1');

    rerender(
      <Harness hidden={false}>
        <CustomElement element={element} />
      </Harness>
    );
    expect(local()).toBe('1');
  });

  it('remounts when the element id changes', async () => {
    const { rerender } = render(
      <Harness>
        <CustomElement element={makeElement()} />
      </Harness>
    );
    await settle();

    fireEvent.click(screen.getByTestId('inc'));
    expect(local()).toBe('1');

    // A different element in the same slot is a different element, and the
    // three cases above would be worthless if this one could not tell.
    rerender(
      <Harness>
        <CustomElement element={makeElement({ id: 'el-2' })} />
      </Harness>
    );

    expect(local()).toBe('0');
  });

  it('fetches the source once for two instances of one name', async () => {
    render(
      <Harness>
        <CustomElement element={makeElement({ id: 'el-1' })} />
        <CustomElement element={makeElement({ id: 'el-2' })} />
      </Harness>
    );
    await settle();

    expect(screen.getAllByTestId('local')).toHaveLength(2);
    expect(fetchSource).toHaveBeenCalledTimes(1);
    expect(fetchSource).toHaveBeenCalledWith('/public/elements/Counter.jsx');
  });

  it('shows the alert for a source that fails and retries on the next mount', async () => {
    fetchSource.mockRejectedValue(new Error('404 Not Found'));

    const { rerender } = render(
      <Harness>
        <CustomElement element={makeElement({ id: 'el-1' })} />
      </Harness>
    );
    await settle();

    expect(screen.getAllByRole('alert')).toHaveLength(1);
    expect(screen.getByRole('alert').textContent).toContain('404 Not Found');

    // Re-rendering the same instance asks nothing: the effect's deps are the
    // name and the client, and neither moved.
    rerender(
      <Harness ctx={{ loading: true }}>
        <CustomElement element={makeElement({ id: 'el-1' })} />
      </Harness>
    );
    await settle();
    expect(fetchSource).toHaveBeenCalledTimes(1);

    // A *new* card of the same name does ask again. `get` rejects on any
    // non-2xx, so the failure may have been a 401 mid-refresh or one offline
    // blip; remembering it would pin the Alert until the page reloads.
    fetchSource.mockResolvedValue({ text: async () => COUNTER_SOURCE });
    rerender(
      <Harness>
        <CustomElement element={makeElement({ id: 'el-1' })} />
        <CustomElement element={makeElement({ id: 'el-2' })} />
      </Harness>
    );
    await settle();

    expect(fetchSource).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId('local')).toBeInTheDocument();
  });

  it('calls back through the element the current props, not the first render', async () => {
    const { rerender } = render(
      <Harness>
        <CustomElement element={makeElement({ name: 'Caller' })} />
      </Harness>
    );
    await settle();

    // `forId` moves the way the server moves it, and is the field a handler
    // frozen at the first render would get wrong.
    rerender(
      <Harness>
        <CustomElement
          element={makeElement({ name: 'Caller', forId: 'step-2' })}
        />
      </Harness>
    );
    fireEvent.click(screen.getByTestId('update'));

    expect(updateElement).toHaveBeenCalledWith(
      expect.objectContaining({
        id: 'el-1',
        forId: 'step-2',
        props: { label: 'z' }
      }),
      'sess-1'
    );
  });

  it('submits to the ask that is open now', async () => {
    render(
      <Harness>
        <CustomElement element={makeElement({ name: 'Caller' })} />
      </Harness>
    );
    await settle();

    // No ask: the gate must swallow the click rather than reply to nothing.
    fireEvent.click(screen.getByTestId('submit'));

    const first: IAsk = {
      callback: vi.fn(),
      spec: { type: 'element', stepId: 'step-1', timeout: 30 }
    };
    act(() => setAsk(first));
    fireEvent.click(screen.getByTestId('submit'));
    expect(first.callback).toHaveBeenCalledWith({
      submitted: true,
      props: { picked: 1 }
    });

    // A second ask replaces the first without the element remounting; the
    // reply must go to the one that is live.
    const second: IAsk = {
      callback: vi.fn(),
      spec: { type: 'element', stepId: 'step-1', timeout: 30 }
    };
    act(() => setAsk(second));
    fireEvent.click(screen.getByTestId('submit'));

    expect(second.callback).toHaveBeenCalledTimes(1);
    expect(first.callback).toHaveBeenCalledTimes(1);
  });

  it('contains a throwing element instead of losing the message list', async () => {
    // React reports a caught error on the console; the boundary does too.
    const noise = vi
      .spyOn(console, 'error')
      .mockImplementation(() => undefined);

    render(
      <Harness>
        <CustomElement element={makeElement({ id: 'el-1', name: 'Thrower' })} />
        <CustomElement element={makeElement({ id: 'el-2' })} />
      </Harness>
    );
    await settle();

    expect(screen.getByRole('alert').textContent).toContain('boom');
    expect(screen.getByTestId('local')).toBeInTheDocument();

    noise.mockRestore();
  });

  it('sees the ask from the element own effect, on the render it opened', async () => {
    render(
      <Harness>
        <CustomElement element={makeElement({ name: 'AutoSubmit' })} />
      </Harness>
    );
    await settle();
    expect(screen.getByTestId('auto')).toBeInTheDocument();

    const ask: IAsk = {
      callback: vi.fn(),
      spec: { type: 'element', stepId: 'step-1', timeout: 30 }
    };
    act(() => setAsk(ask));

    expect(ask.callback).toHaveBeenCalledWith({
      submitted: true,
      props: { auto: true }
    });
  });
});
