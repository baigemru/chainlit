import { useEffect, useState } from 'react';
import { toast } from 'sonner';

import { useConfig } from '@chainlit/react-client';

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle
} from '@/components/ui/alert-dialog';

import { useDeviceKey } from '@/hooks/use-mobile';

/**
 * The phone's one-time offer to move to the desktop version. Whether it shows
 * at all, in which mode and with what words is configuration: an app that did
 * not ask for it gets nothing, and this component never invents a default
 * message of its own.
 *
 * It refuses to render markdown — the text is a sentence, not a document —
 * and it refuses to nag: once the offer has been made it is remembered for as
 * long as the configured frequency says.
 */

type Frequency = 'session' | 'once' | 'always';

const SEEN_KEY = 'chainlit_mobile_notice_seen';

/**
 * `Page` remounts on every Home <-> Thread navigation. Without this,
 * `frequency = "always"` would mean "on every click in the sidebar", and even
 * a remembered dismissal would flash the dialog for the render before the
 * storage read lands.
 */
let offeredThisLoad = false;

/** Specs share a module instance across cases; this is the reset between them. */
export function resetMobileNoticeForTests() {
  offeredThisLoad = false;
}

/**
 * The storage that remembers a dismissal, chosen by the configured frequency.
 * `always` deliberately has none — it is the setting that asks to be asked
 * again.
 *
 * Every touch is guarded, here and below: an embedded webview can throw on
 * mere access to the property, and a notice nobody can dismiss is worse than
 * no notice at all.
 */
const storageFor = (frequency: Frequency): Storage | undefined => {
  try {
    if (frequency === 'session') return window.sessionStorage;
    if (frequency === 'once') return window.localStorage;
  } catch {
    // No storage: the offer stands for this page load and is forgotten.
  }
  return undefined;
};

const wasSeen = (frequency: Frequency): boolean => {
  try {
    return storageFor(frequency)?.getItem(SEEN_KEY) === '1';
  } catch {
    return false;
  }
};

const markSeen = (frequency: Frequency): void => {
  try {
    storageFor(frequency)?.setItem(SEEN_KEY, '1');
  } catch {
    // Same rationale as above.
  }
};

/**
 * A full page load, not a router navigation: `/?device=pc` only pins the
 * device because `use-mobile` reads the query off the URL on boot.
 */
const followLink = (url: string): void => {
  window.location.assign(url);
};

export default function MobileNotice() {
  const { config } = useConfig();
  const device = useDeviceKey();
  const [open, setOpen] = useState(false);

  const notice = config?.ui?.mobile_notice;
  const eligible = !!notice?.enabled && device === 'mobile';

  useEffect(() => {
    // The config arrives asynchronously; a render without it is not an
    // answer, so the once-per-load flag must not be burnt on it.
    if (!notice || !eligible) return;
    if (offeredThisLoad || wasSeen(notice.frequency)) return;
    offeredThisLoad = true;

    if (notice.mode === 'toast') {
      // A toast is not a question. Showing it is the whole offer, so it
      // counts as made the moment it appears — swiping it away and letting
      // it time out are not different answers.
      markSeen(notice.frequency);
      toast(notice.text, {
        duration: 10000,
        action: {
          label: notice.link_label,
          onClick: () => followLink(notice.link_url)
        },
        onDismiss: () => markSeen(notice.frequency),
        onAutoClose: () => markSeen(notice.frequency)
      });
      return;
    }

    setOpen(true);
  }, [eligible, notice]);

  if (!notice || !eligible || notice.mode === 'toast') return null;

  // An empty title is not a heading nobody wrote: the text becomes the
  // heading, because AlertDialogContent without a title is unreachable to a
  // screen reader.
  const heading = notice.title?.trim() ? notice.title : notice.text;
  const hasDescription = heading !== notice.text;

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (next) return;
        // Escape and the overlay close the dialog without ever reaching the
        // cancel button; remembering the dismissal only there would bring
        // the notice back on the next load.
        markSeen(notice.frequency);
        setOpen(false);
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{heading}</AlertDialogTitle>
          {hasDescription ? (
            <AlertDialogDescription>{notice.text}</AlertDialogDescription>
          ) : null}
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>{notice.dismiss_label}</AlertDialogCancel>
          <AlertDialogAction
            onClick={() => {
              markSeen(notice.frequency);
              followLink(notice.link_url);
            }}
          >
            {notice.link_label}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
