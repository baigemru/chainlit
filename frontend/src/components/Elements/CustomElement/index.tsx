/**
 * A custom element that survives everything but its own replacement.
 *
 * The old shape handed `<Runner>` a fresh scope literal on every render, and
 * `Runner` compares `code` and `scope` by identity: any render at all recompiled
 * the source, produced a new React component type and unmounted the host's tree,
 * so whatever the user had typed or toggled inside a card was lost on the start
 * of every run, every Ask, and every `element.update`. Keeping `<Runner>` with a
 * stable scope is not the fix either -- its `shouldComponentUpdate` then returns
 * false forever and the card freezes instead of updating.
 *
 * So: one mutable scope per element id, compiled once, rendered as a component
 * type. `props` is the same object for the element's lifetime and is synced in
 * place from the server's copy; the callbacks are stable trampolines onto a ref.
 * A remount happens only when `element.id` changes or the source text does.
 */
import {
  type ComponentType,
  memo,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState
} from 'react';
import type { Scope } from 'react-runner';
import { useRecoilValue } from 'recoil';

import {
  ChainlitContext,
  IAction,
  ICustomElement,
  IElement,
  askUserState,
  sessionIdState,
  useAuth,
  useChatInteract
} from '@chainlit/react-client';

import Alert from '@/components/Alert';
import { ErrorBoundary } from '@/components/ErrorBoundary';

import Imports from './Imports';
import * as Renderer from './Renderer';
import { compileElement } from './compile';
import { getElementSource } from './source';
import { type UserMessagePayload, buildUserMessage } from './userMessage';

type Handlers = {
  updateElement: (props: Record<string, unknown>) => unknown;
  deleteElement: () => unknown;
  callAction: (action: IAction) => unknown;
  sendUserMessage: (
    message: string,
    payload?: UserMessagePayload,
    command?: string
  ) => unknown;
  submitElement: (props: Record<string, unknown>) => void;
  cancelElement: () => void;
};

type Instance = {
  id: string;
  scope: Scope;
  /** The object the compiled element closes over. Mutated, never replaced. */
  props: Record<string, unknown>;
  /** The `element.props` this instance's `props` were last synced from. */
  synced: unknown;
};

const CustomElement = memo(function CustomElement({
  element
}: {
  element: ICustomElement;
}) {
  const apiClient = useContext(ChainlitContext);
  const sessionId = useRecoilValue(sessionIdState);
  // The ask comes from the store, not from `MessageContext`: that context value
  // is rebuilt on every `loading` change, and subscribing to it is what put a
  // remount at the start and end of every run.
  const askUser = useRecoilValue(askUserState);
  const { sendMessage } = useChatInteract();
  const { user } = useAuth();

  // Seeded from the cache so a second card of a name already fetched renders
  // its element instead of a blank frame while an effect it does not need runs.
  const [sourceCode, setSourceCode] = useState<string | undefined>(
    () => getElementSource(apiClient, element.name).code
  );
  const [error, setError] = useState<string>();

  useEffect(() => {
    let live = true;
    getElementSource(apiClient, element.name).promise.then(
      (code) => {
        if (!live) return;
        setSourceCode(code);
        setError(undefined);
      },
      (err) => {
        if (live) setError(String(err));
      }
    );
    return () => {
      live = false;
    };
  }, [element.name, apiClient]);

  // Rebuilt every render and never handed to the element directly: the element
  // only ever sees the trampolines below, whose identity must not change.
  const handlers: Handlers = {
    updateElement: (nextProps) => {
      if (!sessionId) return;
      const nextElement: IElement = { ...element, props: nextProps };
      return apiClient.updateElement(nextElement, sessionId);
    },
    deleteElement: () => {
      if (!sessionId) return;
      return apiClient.deleteElement(element, sessionId);
    },
    callAction: (action) => {
      if (!sessionId) return;
      return apiClient.callAction(action, sessionId);
    },
    sendUserMessage: (message, payload, command) =>
      sendMessage(
        buildUserMessage(message, user?.identifier || 'User', payload, command)
      ),
    submitElement: (props) => {
      if (
        askUser?.spec.type === 'element' &&
        askUser.spec.stepId === element.forId &&
        !askUser.awaitingReply
      ) {
        askUser.callback({ submitted: true, props });
      }
    },
    cancelElement: () => {
      if (
        askUser?.spec.type === 'element' &&
        askUser.spec.stepId === element.forId &&
        !askUser.awaitingReply
      ) {
        askUser.callback({ submitted: false });
      }
    }
  };

  // Assigned in the render body, not in an effect. React flushes passive
  // effects child-first, so an element that calls `submitElement` from its own
  // `useEffect` would read the *previous* render's handlers -- and miss the ask
  // on exactly the render where it opened.
  const latest = useRef(handlers);
  latest.current = handlers;

  const instance = useRef<Instance | null>(null);
  // Before the compile below, which binds this exact scope into the element as
  // `new Function` parameters: a scope built after it would never be read.
  if (!instance.current || instance.current.id !== element.id) {
    const props: Record<string, unknown> = {};
    instance.current = {
      id: element.id,
      props,
      synced: null,
      scope: {
        import: { ...Imports, '@/components/renderer': Renderer },
        props,
        // Bound once, unlike the callbacks: the app builds one client for the
        // life of the page, and a scope key cannot be made lazy -- it is a
        // `new Function` parameter, read at compile time.
        apiClient,
        updateElement: (next: Record<string, unknown>) =>
          latest.current.updateElement(next),
        deleteElement: () => latest.current.deleteElement(),
        callAction: (action: IAction) => latest.current.callAction(action),
        sendUserMessage: (
          message: string,
          payload?: UserMessagePayload,
          command?: string
        ) => latest.current.sendUserMessage(message, payload, command),
        submitElement: (props: Record<string, unknown>) =>
          latest.current.submitElement(props),
        cancelElement: () => latest.current.cancelElement()
      }
    };
  }

  if (instance.current.synced !== element.props) {
    instance.current.synced = element.props;
    // A deep copy, as before: the host mutates this object in place
    // (`updateElement(Object.assign(props, …))` is the documented idiom) and
    // must not be able to reach into the store's copy of the element.
    const next = JSON.parse(JSON.stringify(element.props)) as Record<
      string,
      unknown
    >;
    const live = instance.current.props;
    for (const key of Object.keys(live)) {
      if (!(key in next)) delete live[key];
    }
    Object.assign(live, next);
  }

  const scope = instance.current.scope;
  const compiled = useMemo(() => {
    if (!sourceCode) return undefined;
    try {
      return { render: compileElement(sourceCode, scope), error: undefined };
    } catch (err) {
      return {
        render: undefined,
        error: err instanceof Error ? err.message : String(err)
      };
    }
    // `scope` is stable for the life of an element id and is left out of the
    // deps for that reason: a recompile is a new component type, and a new
    // component type is a remount.
  }, [sourceCode, element.id]);

  if (error) return <Alert variant="error">{error}</Alert>;
  if (!sourceCode || !compiled) return null;
  if (!compiled.render) {
    return <Alert variant="error">{compiled.error}</Alert>;
  }

  const Compiled: ComponentType = compiled.render;

  return (
    <div className={`${element.display}-custom flex flex-col flex-grow`}>
      {/* A throw inside the host's own render used to be caught by `<Runner>`
          and latched by the parent, replacing the whole card with an Alert
          that nothing cleared. Same latch, narrower blast radius: the shared
          boundary keeps the rest of the message list alive and logs the throw,
          and `key` scopes the latch to this element. */}
      <ErrorBoundary
        key={element.id}
        prefix={`Element ${element.name} failed.`}
      >
        <Compiled />
      </ErrorBoundary>
    </div>
  );
});

export default CustomElement;
