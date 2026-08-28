import { cn } from '@/lib/utils';
import { memo, useMemo } from 'react';

import { IMessageElement, IStep } from '@chainlit/react-client';

import { InlinedElements } from './Content/InlinedElements';

interface Props {
  message: IStep;
  elements: IMessageElement[];
}

const UserMessage = memo(function UserMessage({
  message,
  elements,
  children
}: React.PropsWithChildren<Props>) {
  const inlineElements = useMemo(() => {
    return elements.filter(
      (el) => el.forId === message.id && el.display === 'inline'
    );
  }, [message.id, elements]);

  return (
    <div className="flex flex-col w-full gap-1">
      <InlinedElements elements={inlineElements} className="items-end" />

      <div className="flex flex-row items-center gap-1 w-full group">
        <div
          className={cn(
            'px-5 py-2.5 relative bg-accent rounded-3xl',
            inlineElements.length ? 'rounded-tr-lg' : '',
            'max-w-[70%] flex-grow-0 ml-auto'
          )}
        >
          <div className="flex flex-col">
            {message.command ? (
              <div className="font-bold text-[#08f] command-span">
                {message.command}
              </div>
            ) : null}
            {children}
          </div>
        </div>
      </div>
    </div>
  );
});

export default UserMessage;
