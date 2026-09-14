import { useContext, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import Page from 'pages/Page';

import {
  ChainlitContext,
  IMessageElement,
  useApi,
  useChatData,
  useConfig
} from '@chainlit/react-client';

import Alert from '@/components/Alert';
import { ElementView } from '@/components/ElementView';
import { Loader } from '@/components/Loader';

import { useQuery } from 'hooks/query';

export default function Element() {
  const { id } = useParams();
  const query = useQuery();
  const { elements } = useChatData();
  const { config } = useConfig();
  const apiClient = useContext(ChainlitContext);

  const [element, setElement] = useState<IMessageElement | null>(null);
  const navigate = useNavigate();

  const threadId = query.get('thread');

  const dataPersistence = config?.dataPersistence;

  const { data, isLoading, error } = useApi<IMessageElement>(
    id && threadId && dataPersistence
      ? `/project/thread/${threadId}/element/${id}`
      : null
  );

  // A copy, not a mutation: `data` is SWR's cached object. The persisted url
  // is app-relative and the page origin is the wrong base for it. Memoized
  // because the effect below lists `element` among its dependencies —
  // building the copy inside it would set a new object on every run and
  // re-enter through its own state change until React gives up.
  const resolved = useMemo(
    () =>
      data ? { ...data, url: apiClient.resolveElementUrl(data.url) } : null,
    [data, apiClient]
  );

  useEffect(() => {
    if (resolved) {
      setElement(resolved);
    } else if (id && !dataPersistence && !element) {
      const foundElement = elements.find((element) => element.id === id);

      if (foundElement) {
        setElement(foundElement);
      }
    }
  }, [resolved, dataPersistence, element, elements, id, threadId]);

  return (
    <Page>
      <>
        {isLoading ? (
          <div className="flex flex-grow justify-center items-center">
            <Loader className="!size-6" />
          </div>
        ) : null}
        {error ? <Alert variant="error">{error.message}</Alert> : null}
        {element ? (
          <ElementView element={element} onGoBack={() => navigate('/')} />
        ) : null}
      </>
    </Page>
  );
}
