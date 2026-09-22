import { cn } from '@/lib/utils';

import { IAction } from '@chainlit/react-client';

import { useIsMobile } from '@/hooks/use-mobile';

import { ActionButton } from './ActionButton';

interface Props {
  actions: IAction[];
}

/**
 * The row of things to do after an answer, and the chips under it.
 *
 * Two rows, not one: a chip carries a question ("weight of the box?") and
 * belongs in its own wrapping line, while the commands are the offers. On a
 * phone the commands go two to a line -- a column of four turns the offer
 * into a scroll, and the storyboard's row of four is what the numbers are
 * measured against. Chips wrap at their own width on every screen.
 */
export default function MessageActions({ actions }: Props) {
  const isMobile = useIsMobile();

  const chips = actions.filter((a) => a.variant === 'chip');
  const commands = actions.filter((a) => a.variant !== 'chip');

  return (
    <>
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
      {chips.length ? (
        // `w-full` inside the wrapping flex the message buttons live in:
        // the chips are a line of their own, not the tail of the commands.
        <div
          data-role="message-action-chips"
          className="flex flex-wrap gap-1 w-full"
        >
          {chips.map((a) => (
            <ActionButton action={a} key={a.id} />
          ))}
        </div>
      ) : null}
    </>
  );
}
