import { cn } from '@/lib/utils';

import { IAction } from '@chainlit/react-client';

import { useIsMobile } from '@/hooks/use-mobile';

import { ActionButton } from './ActionButton';

interface Props {
  actions: IAction[];
}

/**
 * The chips after an answer, and the row of things to do under them.
 *
 * Two rows, not one: a chip carries a question ("weight of the box?") and
 * belongs in a line of its own, while the commands are the offers. Chips
 * first, because a chip refines the answer just read and a command leaves
 * it: the question sits next to what it questions, and the offer closes the
 * message.
 *
 * On a phone the commands go two to a line -- a column of four turns the
 * offer into a scroll, and the storyboard's row of four is what the numbers
 * are measured against -- and the chips become one strip that scrolls
 * sideways, its right edge fading to say there is more. Wrapped, six chips
 * are three lines of the screen spent before the first command. On a wider
 * screen chips wrap at their own width.
 */
export default function MessageActions({ actions }: Props) {
  const isMobile = useIsMobile();

  const chips = actions.filter((a) => a.variant === 'chip');
  const commands = actions.filter((a) => a.variant !== 'chip');

  return (
    <>
      {chips.length ? (
        // `w-full` inside the wrapping flex the message buttons live in:
        // the chips are a line of their own, not the head of the commands.
        <div
          data-role="message-action-chips"
          className={cn(
            'flex gap-1 w-full',
            isMobile
              ? // The fade is a mask, not an overlay, so it needs no colour
                // of its own and holds on any background. `pr-8` lets the
                // last chip scroll clear of it; without that the end of the
                // strip would always be half faded. The children must not
                // shrink, or a nowrap strip squeezes chips instead of
                // scrolling them.
                'flex-nowrap overflow-x-auto pr-8 [scrollbar-width:none] [&>*]:shrink-0 [mask-image:linear-gradient(90deg,#000_86%,transparent)]'
              : 'flex-wrap'
          )}
        >
          {chips.map((a) => (
            <ActionButton action={a} key={a.id} />
          ))}
        </div>
      ) : null}
      {commands.length ? (
        <div
          data-role="message-actions"
          className={cn(
            'gap-1',
            isMobile
              ? 'grid grid-cols-2 items-stretch w-full'
              : 'flex items-center flex-wrap'
          )}
        >
          {commands.map((a) => (
            <ActionButton action={a} key={a.id} />
          ))}
        </div>
      ) : null}
    </>
  );
}
