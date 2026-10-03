/**
 * An element that sends a photo has to reach `on_message` as the composer
 * does: the file as an `Image` in `message.elements`, which the server builds
 * only from `message.send.fileReferences`. These cases drive the flow a host
 * element runs -- upload through `useChatInteract().uploadFile` from the
 * import map, then `sendUserMessage` with the resolved ids -- and look at the
 * two arguments the transport would put on the wire.
 */
import { act, fireEvent, render, screen } from '@testing-library/react';
import { RecoilRoot } from 'recoil';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  type ChainlitAPI,
  ChainlitContext,
  type ICustomElement
} from '@chainlit/react-client';

import CustomElement from '@/components/Elements/CustomElement';
import Imports from '@/components/Elements/CustomElement/Imports';
import { resetElementSourceCache } from '@/components/Elements/CustomElement/source';

const spies = vi.hoisted(() => ({
  sendMessage: vi.fn(),
  uploadFile: vi.fn()
}));

// No block comment directly above this call: it stops the mock being hoisted
// (see customElementStability.spec.tsx). The atoms are made in the factory
// because the built package and this spec hold two Recoil module instances.
vi.mock('@chainlit/react-client', async () => {
  const { atom } = await import('recoil');
  const { createContext } = await import('react');
  return {
    sessionIdState: atom<string | undefined>({
      key: 'files-test/SessionId',
      default: 'sess-1'
    }),
    askUserState: atom<unknown>({
      key: 'files-test/AskUser',
      default: undefined
    }),
    ChainlitContext: createContext(undefined),
    useAuth: () => ({ user: { identifier: 'alice' } }),
    useChatInteract: () => ({
      sendMessage: spies.sendMessage,
      uploadFile: spies.uploadFile
    })
  };
});

// What ResearchLaunch does, reduced to the two calls that matter.
const LAUNCH_SOURCE = `
import { useChatInteract } from '@chainlit/react-client';

export default function Launch() {
  const { uploadFile } = useChatInteract();
  const start = async () => {
    const file = new File(['x'], 'photo.png', { type: 'image/png' });
    const { promise } = uploadFile(file, () => {});
    const { id } = await promise;
    sendUserMessage('', { kind: 'research.launch', requirements: { moq_max: 100 } }, undefined, [{ id }]);
  };
  return <button data-testid="start" onClick={start}>start</button>;
}
`;

const PLAIN_SOURCE = `
export default function Plain() {
  return <button data-testid="plain" onClick={() => sendUserMessage('hi', { a: 1 })}>p</button>;
}
`;

const SOURCES: Record<string, string> = {
  Launch: LAUNCH_SOURCE,
  Plain: PLAIN_SOURCE
};

const apiClient = {
  httpEndpoint: 'http://test',
  get: async (endpoint: string) => {
    const name = endpoint.replace('/public/elements/', '').replace('.jsx', '');
    return { text: async () => SOURCES[name] };
  }
} as unknown as ChainlitAPI;

const makeElement = (name: string): ICustomElement => ({
  id: `el-${name}`,
  type: 'custom',
  name,
  display: 'inline',
  forId: 'step-1',
  props: {}
});

const mount = (name: string) =>
  render(
    <RecoilRoot>
      <ChainlitContext.Provider value={apiClient as never}>
        <CustomElement element={makeElement(name)} />
      </ChainlitContext.Provider>
    </RecoilRoot>
  );

const settle = async () => {
  await act(async () => {
    for (let i = 0; i < 4; i++) await Promise.resolve();
  });
};

beforeEach(() => {
  resetElementSourceCache();
  spies.sendMessage.mockReset();
  spies.uploadFile.mockReset();
  spies.uploadFile.mockImplementation(() => ({
    xhr: {},
    promise: Promise.resolve({ id: 'file-1' })
  }));
});

describe('custom element sending files', () => {
  it('hands the uploaded ids to the transport as fileReferences', async () => {
    mount('Launch');
    await settle();

    fireEvent.click(screen.getByTestId('start'));
    await settle();

    expect(spies.uploadFile).toHaveBeenCalledTimes(1);
    expect(spies.sendMessage).toHaveBeenCalledTimes(1);
    const [step, fileReferences] = spies.sendMessage.mock.calls[0];
    expect(fileReferences).toEqual([{ id: 'file-1' }]);
    // The payload still nests under its own key beside the client's stamp.
    expect(step.metadata).toEqual({
      location: window.location.href,
      payload: { kind: 'research.launch', requirements: { moq_max: 100 } }
    });
    expect(step.type).toBe('user_message');
    expect(step.name).toBe('alice');
  });

  it('sends no files when the element passes none', async () => {
    mount('Plain');
    await settle();

    fireEvent.click(screen.getByTestId('plain'));

    const [step, fileReferences] = spies.sendMessage.mock.calls[0];
    // `sendMessage` defaults an absent list to `[]`; what matters is that an
    // element written before the argument existed sends what it sent then.
    expect(fileReferences).toBeUndefined();
    expect(step.metadata.payload).toEqual({ a: 1 });
  });
});

describe('custom element import map', () => {
  it('offers the alert dialog', () => {
    const mod = (Imports as Record<string, Record<string, unknown>>)[
      '@/components/ui/alert-dialog'
    ];
    for (const name of [
      'AlertDialog',
      'AlertDialogTrigger',
      'AlertDialogContent',
      'AlertDialogTitle',
      'AlertDialogDescription',
      'AlertDialogAction',
      'AlertDialogCancel'
    ]) {
      expect(mod?.[name], name).toBeDefined();
    }
  });
});
