import { useEffect, useRef } from 'react';

import type { SessionHandoff } from '@chainlit/react-client';
import { useChatTransport } from '@chainlit/react-client';

import { useSessionHandoff } from '@/hooks/useSessionHandoff';

export default function ChatProfileSwitchListener() {
  const transport = useChatTransport();
  const handoff = useSessionHandoff();

  // The latest hand-off closure lives in a ref so the socket subscription
  // below is only re-registered when the socket itself changes. The hook is
  // rebuilt on every render (it reads the config and the current profile);
  // re-subscribing on each one would tear the listener off the transport in
  // the middle of a frame.
  const switchRef = useRef<(payload: SessionHandoff) => void>();
  switchRef.current = handoff;

  // Subscribed to the transport, not to a socket: the listener outlives
  // every connection the transport builds, so nothing re-registers it.
  useEffect(
    () =>
      transport.onMessage((message) => {
        if (message.t === 'session.handoff') switchRef.current?.(message);
      }),
    [transport]
  );

  return null;
}
