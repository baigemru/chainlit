import capitalize from 'lodash/capitalize';
import { useContext, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { toast } from 'sonner';

import Page from 'pages/Page';

import {
  ChainlitContext,
  type IAccountActionResponse,
  type IAccountPage,
  cloneClient,
  useApi,
  useAuth,
  useConfig
} from '@chainlit/react-client';

import Alert from '@/components/Alert';
import SchemaForm from '@/components/SchemaForm';
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar';
import { Skeleton } from '@/components/ui/skeleton';
import { useTranslation } from 'components/i18n/Translator';

import { useLayoutMaxWidth } from '@/hooks/useLayoutMaxWidth';
import { useSessionHandoff } from '@/hooks/useSessionHandoff';

/**
 * The account page: whatever the application declared with `@cl.account`.
 *
 * The page knows nothing about the fields. It fetches a JSON Schema and a
 * values object, hands both to `SchemaForm`, and puts the values back. That
 * is the whole point of the msgspec Struct being the single source: an
 * application that adds a field ships no client code.
 *
 * It mounts inside `Page`, so the socket `App.tsx` owns stays attached and
 * the chat is still there when the user navigates back.
 */
export default function Account() {
  const { user } = useAuth();
  const { config } = useConfig();
  const apiClient = useContext(ChainlitContext);
  const layoutMaxWidth = useLayoutMaxWidth();
  const { t } = useTranslation();
  const handoff = useSessionHandoff();

  // The address bar is the only tab state there is, so a tab is linkable and
  // survives a reload. Nothing writes the first tab into the URL: a page
  // opened without `?tab=` stays clean and `SchemaForm` falls back to the
  // first one on its own.
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab = searchParams.get('tab') ?? undefined;

  const { data, error, isLoading, mutate } =
    useApi<IAccountPage>('/project/account');

  // `APIBase.fetch` hands every failure to the context client's `onError`,
  // which toasts `Bad Request: <detail>`. The page says it better -- the
  // server's `detail` alone carries the field path, and it is the only field
  // addressing this form has -- so the save goes out through a copy with that
  // hook off, the way `useApi` silences it for reads. `on401` stays: a cookie
  // that expired mid-save must still reach the login page.
  const saveClient = useMemo(() => {
    const copy = cloneClient(apiClient);
    copy.onError = undefined;
    return copy;
  }, [apiClient]);

  const heading = config?.ui?.account?.title || t('account.title');
  const displayName = user?.display_name || user?.identifier || '';

  // Duck-typed, not `instanceof ClientError`: SWR rethrows whatever the
  // fetcher threw, and a narrowing that needs the class would tie this page
  // to a runtime import it does not otherwise need.
  const status = (error as { status?: number } | undefined)?.status;

  const onSubmit = async (values: Record<string, unknown>) => {
    try {
      // `put` resolves with the `Response`, not the parsed body.
      const res = await saveClient.put('/project/account', values);
      const page = (await res.json()) as IAccountPage;
      // `false`: the server just answered with the stored state, so a
      // revalidation would only ask it to repeat itself.
      mutate(page, false);
      toast.success(page.message || t('account.saved'));
    } catch (err) {
      const failure = err as { detail?: string; message?: string };
      toast.error(failure.detail || failure.message);
      // Rethrown, not swallowed: the form is what disabled itself for the
      // round trip, and only the rejection puts the Save button back.
      throw err;
    }
  };

  /**
   * A button declared by `x-actions` was pressed.
   *
   * The page does not know what the action means; it posts the element the
   * form is holding and applies whichever of the three outcomes came back.
   * Same silenced client as the save, for the same reason: one failure, one
   * toast, carrying the server's `detail`.
   */
  const onAction = async (name: string, path: string, item: unknown | null) => {
    try {
      const res = await saveClient.post(`/project/account/actions/${name}`, {
        path,
        item
      });
      const { outcome } = (await res.json()) as IAccountActionResponse;
      switch (outcome.t) {
        case 'toast':
          toast.success(outcome.message);
          break;
        case 'page':
          // The hook answered with the page rebuilt, exactly as a save does,
          // so the form adopts it without a second round trip.
          mutate(outcome.page, false);
          if (outcome.message) toast.success(outcome.message);
          break;
        case 'open_thread':
          // The very path a `session.handoff` frame takes, through the shared
          // hook: the server has already minted the successor thread and
          // parked the hand-over record under it, so this is a hand-off that
          // happens to have been asked for over HTTP.
          handoff({
            t: 'session.handoff',
            chatProfile: outcome.chat_profile,
            nextThreadId: outcome.thread_id ?? undefined,
            keepTranscript: false,
            hasTransitMessage: outcome.has_transit_message
          });
          break;
      }
    } catch (err) {
      const failure = err as { detail?: string; message?: string };
      toast.error(failure.detail || failure.message);
      // Not rethrown, unlike the save: a card button has nothing to restore
      // but itself, and it is re-enabled by this promise settling either
      // way. A rejection here would only reach the form as an unhandled one.
    }
  };

  const body = () => {
    if (isLoading) {
      return (
        <div className="flex flex-col gap-4">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-2/3" />
        </div>
      );
    }
    if (status === 404) {
      return <Alert variant="info">{t('account.notConfigured')}</Alert>;
    }
    if (error) {
      return (
        <Alert variant="error">
          {(error as { detail?: string }).detail || error.message}
        </Alert>
      );
    }
    if (!data) return null;
    return (
      <>
        {data.readonly ? (
          <Alert variant="info">{t('account.readonly')}</Alert>
        ) : null}
        <SchemaForm
          schema={data.schema}
          values={data.values}
          readonly={data.readonly}
          onSubmit={onSubmit}
          onAction={onAction}
          activeTab={activeTab}
          onTabChange={(name) =>
            // Replace, not push: Back leaves the account page rather than
            // walking backwards through the tabs the user looked at. A copy
            // of the previous params, so a `?tab=` change keeps whatever
            // else the address carries.
            setSearchParams(
              (previous) => {
                const next = new URLSearchParams(previous);
                next.set('tab', name);
                return next;
              },
              { replace: true }
            )
          }
        />
      </>
    );
  };

  return (
    <Page>
      <div
        className="flex flex-col flex-grow gap-6 mx-auto w-full p-4"
        style={{ maxWidth: layoutMaxWidth }}
      >
        <div className="flex items-center gap-4">
          <Avatar className="h-12 w-12">
            <AvatarImage src={user?.metadata?.image} alt="user image" />
            <AvatarFallback className="bg-primary text-primary-foreground font-semibold">
              {capitalize(displayName[0])}
            </AvatarFallback>
          </Avatar>
          <div className="flex flex-col min-w-0">
            <p className="text-sm font-medium leading-none truncate">
              {displayName}
            </p>
            <p className="text-sm text-muted-foreground truncate">
              {user?.identifier}
            </p>
          </div>
        </div>
        <div className="flex flex-col">
          <h1 className="text-lg font-bold">{heading}</h1>
          <p className="text-sm text-muted-foreground">
            {t('account.description')}
          </p>
        </div>
        {body()}
      </div>
    </Page>
  );
}
