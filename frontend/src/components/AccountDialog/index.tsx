import { cn } from '@/lib/utils';
import { useContext, useMemo } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { toast } from 'sonner';

import {
  ChainlitContext,
  type IAccountActionResponse,
  type IAccountPage,
  type IAccountSave,
  cloneClient,
  useApi,
  useConfig
} from '@chainlit/react-client';

import Alert from '@/components/Alert';
import SchemaForm from '@/components/SchemaForm';
import { useTranslation } from '@/components/i18n/Translator';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle
} from '@/components/ui/dialog';
import { Skeleton } from '@/components/ui/skeleton';

import { useIsMobile } from '@/hooks/use-mobile';
import { useSessionHandoff } from '@/hooks/useSessionHandoff';

import AccountLayout from './Layout';

/**
 * The account, as a modal over the chat the user was in.
 *
 * `/account` is still a route — it is linkable, it carries `?tab=`, and its
 * element is the chat home — so what shows through the blurred overlay is the
 * live session, not a screenshot of one. The dialog is open exactly when the
 * address says so and nothing else opens it.
 */
export default function AccountDialog() {
  const location = useLocation();
  const navigate = useNavigate();

  // Mounted only while the address asks for it, so the fetch below never runs
  // on another route. `GET /project/account` is the occasion on which the
  // application marks things seen — the engine recomputes the badge and
  // pushes it after every one — so a hook that ran on every page would clear
  // the dot for a user who never opened the account.
  if (location.pathname !== '/account') return null;

  const close = () => {
    // `default` is react-router's own marker for an entry it did not create:
    // the account was opened straight from the address bar, there is nothing
    // behind it, and a `-1` would leave the app for whatever page the tab
    // held before.
    if (location.key === 'default') navigate('/', { replace: true });
    else navigate(-1);
  };

  return <AccountBody onClose={close} />;
}

function AccountBody({ onClose }: { onClose: () => void }) {
  const { config } = useConfig();
  const apiClient = useContext(ChainlitContext);
  const { t } = useTranslation();
  const handoff = useSessionHandoff();
  const isMobile = useIsMobile();

  const { data, error, isLoading, mutate } =
    useApi<IAccountPage>('/project/account');

  // `APIBase.fetch` hands every failure to the context client's `onError`,
  // which toasts `Bad Request: <detail>`. The dialog says it better -- the
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

  // Duck-typed, not `instanceof ClientError`: SWR rethrows whatever the
  // fetcher threw, and a narrowing that needs the class would tie this
  // component to a runtime import it does not otherwise need.
  const status = (error as { status?: number } | undefined)?.status;

  const onSubmit = async (
    values: Record<string, unknown>,
    base: Record<string, unknown>
  ) => {
    try {
      // Both halves, because the engine stores the difference between them:
      // the account document has a second writer — the application, from a
      // background arrival — and a save of the whole document would drop
      // whatever landed while this page was open. `base` is the page the form
      // was filled from, and the route refuses a save without one.
      const save: IAccountSave = { values, base };
      // `put` resolves with the `Response`, not the parsed body.
      const res = await saveClient.put('/project/account', save);
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
   * The dialog does not know what the action means; it posts the element the
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
          // happens to have been asked for over HTTP. The navigation it ends
          // in leaves `/account`, so the dialog closes with no extra code.
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
        <div className="flex flex-1 flex-col gap-4 p-6">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-2/3" />
        </div>
      );
    }
    if (status === 404) {
      return (
        <div className="flex flex-1 flex-col p-6">
          <Alert variant="info">{t('account.notConfigured')}</Alert>
        </div>
      );
    }
    if (error) {
      return (
        <div className="flex flex-1 flex-col p-6">
          <Alert variant="error">
            {(error as { detail?: string }).detail || error.message}
          </Alert>
        </div>
      );
    }
    if (!data) return null;
    return (
      <SchemaForm
        schema={data.schema}
        values={data.values}
        readonly={data.readonly}
        onSubmit={onSubmit}
        onAction={onAction}
        className={cn(
          'flex h-full min-h-0 w-full overflow-hidden',
          isMobile ? 'flex-col' : 'flex-row'
        )}
      >
        <AccountLayout />
      </SchemaForm>
    );
  };

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        // Radix reports the ×, Esc and the overlay click through this one
        // callback, and all three mean the same thing here: the address goes
        // back to where the account was opened from.
        if (!open) onClose();
      }}
    >
      <DialogContent
        closeLabel={t('account.close')}
        className={cn(
          'flex h-[85vh] w-[92vw] max-w-5xl gap-0 overflow-hidden p-0 sm:rounded-xl',
          // A phone has no room for a window over a window: the account takes
          // the screen. `sm:rounded-none` as well as `rounded-none` because
          // `useIsMobile` breaks at 768px while the `sm:` variant starts at
          // 640, and between the two the corners would be rounded on a
          // full-screen sheet.
          isMobile &&
            'inset-0 left-0 top-0 h-full w-full max-w-none translate-x-0 translate-y-0 rounded-none sm:rounded-none'
        )}
      >
        {/* Radix requires both, and reading them out is the only announcement
            a screen reader gets: the heading is drawn per section on the
            right, not once at the top. `asChild` because Radix's Title is an
            `h2` of its own, and the section headings are the h2s here. */}
        <DialogTitle asChild>
          <h1 className="sr-only">{heading}</h1>
        </DialogTitle>
        <DialogDescription className="sr-only">
          {t('account.description')}
        </DialogDescription>
        {body()}
      </DialogContent>
    </Dialog>
  );
}
