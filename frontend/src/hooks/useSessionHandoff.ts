import { flushSync } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import { useSetRecoilState } from 'recoil';

import type { SessionHandoff } from '@chainlit/react-client';
import {
  IStep,
  askUserState,
  loadingState,
  messagesState,
  useChatInteract,
  useChatSession,
  useConfig
} from '@chainlit/react-client';

import { freezeStreaming } from '@/components/chat/MessagesContainer/transcript';

import {
  IAttachment,
  IChatBoundary,
  attachmentsState,
  chatBoundariesState,
  collapsedExcursionsState,
  keptExcursionsState,
  openThreadTransitionState
} from '@/state/chat';

/**
 * Take a hand-off: leave the current session and adopt the successor the
 * server minted.
 *
 * It lives in a hook rather than in `ChatProfileSwitchListener` because there
 * are two ways to arrive at one now — the `session.handoff` frame, and an
 * `open_thread` outcome the account page gets back from an account action.
 * Both must do the *same* thing, teardown order included, so there is one
 * implementation and two callers; the listener keeps only its subscription.
 */
export const useSessionHandoff = (): ((payload: SessionHandoff) => void) => {
  const navigate = useNavigate();
  const { config } = useConfig();
  const { chatProfile } = useChatSession();
  const { clear } = useChatInteract();
  const setAskUser = useSetRecoilState(askUserState);
  const setLoading = useSetRecoilState(loadingState);
  const setMessages = useSetRecoilState(messagesState);
  const setBoundaries = useSetRecoilState(chatBoundariesState);
  const setKeptExcursions = useSetRecoilState(keptExcursionsState);
  const setCollapsedExcursions = useSetRecoilState(collapsedExcursionsState);
  const setAttachments = useSetRecoilState<IAttachment[]>(attachmentsState);
  const setTransition = useSetRecoilState(openThreadTransitionState);

  return (payload: SessionHandoff) => {
    const { chatProfile: name, hasTransitMessage, nextThreadId } = payload;
    const keepTranscript = !!payload.keepTranscript;

    if (!config?.chatProfiles?.some((profile) => profile.name === name)) {
      console.warn(
        `session.handoff: unknown chat profile "${name}", ignoring.`
      );
      return;
    }

    const alreadyActive = chatProfile === name;

    // Keeping the transcript is never a no-op: it draws a line and starts a
    // new thread, which is meaningful even within the same profile. So is a
    // parked transit message — leaving it unclaimed would strand it.
    if (alreadyActive && !keepTranscript && !hasTransitMessage) return;

    // Same path as a manual selection (ChatProfiles.handleConfirm), minus
    // the confirmation dialog: the server already made the decision.
    //
    // flushSync keeps the whole teardown in ONE commit, and it is still
    // required after the resume driver moved into ThreadAddressSync -- more
    // so, because that component is mounted on every route rather than only
    // under /thread/:id. A manual selection runs in a discrete React event,
    // so its state updates and the router update share a lane; here we run
    // in a socket callback, where the Recoil writes are scheduled at sync
    // priority while the router update is not. The split would commit a
    // render still located on /thread/<old> with the descriptor already
    // asking for <next>: ThreadAddressSync reads a route naming neither the
    // requested nor the current thread and clears the session this hand-off
    // has just created, into <old>.
    flushSync(() => {
      setAskUser(undefined);
      setLoading(false);
      setAttachments([]);

      // Read through updaters so these are the values before clear() wipes
      // them, without subscribing this component to every streamed token.
      let kept: IStep[] | undefined;
      let keptBoundaries: IChatBoundary[] = [];
      if (keepTranscript) {
        setMessages((previous) => {
          kept = freezeStreaming(previous);
          return previous;
        });
        setBoundaries((previous) => {
          keptBoundaries = previous;
          return previous;
        });
      }

      // The real teardown, so this path inherits whatever it grows upstream.
      // The successor is stated in full rather than assembled: the thread the
      // backend parked the hand-off record under and the profile it switches
      // to are one decision, and the connect effect must never see half of
      // it. Descriptor first, navigation below -- the other order is the
      // split this flushSync exists to prevent, committed deliberately.
      clear({ threadId: nextThreadId || undefined, chatProfile: name });

      if (keepTranscript && kept === undefined) {
        console.error(
          'session.handoff: could not read the transcript; keeping the chat cleared.'
        );
      }

      const afterMessageId = kept?.at(-1)?.id;
      if (kept?.length && afterMessageId) {
        setMessages(kept);
        // A boundary already on that message would be overwritten by the new
        // one, silently dropping the divider it drew.
        setBoundaries([
          ...keptBoundaries.filter((b) => b.afterMessageId !== afterMessageId),
          { afterMessageId, profile: name }
        ]);
      } else {
        setBoundaries([]);
      }

      // A hard switch blanks the screen; excursions kept by earlier returns
      // to a parent thread would otherwise linger above the fresh chat. A
      // soft switch keeps everything on screen, excursions included.
      if (!keepTranscript) {
        setKeptExcursions([]);
        setCollapsedExcursions({});
      }

      // Push, not replace: a profile switch is a new conversation, so Back
      // leads to the one it came from. The transition is what keeps `Chat`
      // mounted over the kept transcript while /thread/<next> waits for its
      // `session.ready` -- without it `Thread.tsx` would swap the transcript
      // this hand-off just preserved for a loader. ThreadReturnListener
      // retires it when the thread becomes current.
      //
      // Only for an app that can resume, which is the same rule the address
      // follows everywhere else: in an app without resume hooks
      // `Thread.tsx` renders /thread/<id> as `ReadOnlyThread`, whose fetch
      // of a thread with no row yet 404s and sends the user home -- so a
      // switch there would bounce out of the chat it had just created. Such
      // an app goes to `/` and stays unnamed, exactly as before.
      if (nextThreadId && config?.threadResumable) {
        setTransition({ threadId: nextThreadId, keepTranscript });
        navigate(`/thread/${nextThreadId}`);
      } else {
        navigate('/');
      }
    });
  };
};
