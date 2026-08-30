import { cn } from '@/lib/utils';
import { ArrowDown } from 'lucide-react';
import {
  MutableRefObject,
  useCallback,
  useEffect,
  useRef,
  useState
} from 'react';

import { useChatMessages } from '@chainlit/react-client';

import { Button } from '@/components/ui/button';

interface Props {
  autoScrollUserMessage?: boolean;
  autoScrollAssistantMessage?: boolean;
  assistantMessageAnchor?: 'bottom' | 'top';
  autoScrollRef?: MutableRefObject<boolean>;
  children: React.ReactNode;
  className?: string;
}

export default function ScrollContainer({
  autoScrollRef,
  autoScrollUserMessage,
  autoScrollAssistantMessage,
  assistantMessageAnchor,
  children,
  className
}: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const spacerRef = useRef<HTMLDivElement>(null);
  const anchorRef = useRef<HTMLDivElement | null>(null);
  // The element the view was last moved to. A streaming reply rewrites
  // `messages` on every token; without this the smooth scroll would be
  // re-issued each time and fight the reader's own scrolling.
  const scrolledToRef = useRef<HTMLDivElement | null>(null);
  const { messages } = useChatMessages();
  const [showScrollButton, setShowScrollButton] = useState(false);
  const [isScrolling, setIsScrolling] = useState(false);

  // "top" makes an assistant reply behave like a user message: it is brought
  // to the top of the viewport and left there while it grows. A reply that is
  // one tall element — a feed of product cards — otherwise drops the reader on
  // its last card.
  const anchorsTheAssistant =
    !!autoScrollAssistantMessage && assistantMessageAnchor === 'top';

  // The element the spacer sizes itself around. In "bottom" mode this is the
  // last user message, exactly as it always was. In "top" mode an assistant
  // reply competes for the job, and a nested step must not take the anchor
  // from the message that renders it — hence the top-level filter.
  const findAnchor = useCallback((): HTMLDivElement | null => {
    if (!ref.current) return null;

    if (!anchorsTheAssistant) {
      const userMessages = ref.current.querySelectorAll<HTMLDivElement>(
        '[data-step-type="user_message"]'
      );
      return userMessages[userMessages.length - 1] ?? null;
    }

    const candidates = ref.current.querySelectorAll<HTMLDivElement>(
      '[data-step-type="user_message"], [data-step-type="assistant_message"]'
    );
    for (let i = candidates.length - 1; i >= 0; i--) {
      if (!candidates[i].parentElement?.closest('[data-step-type]')) {
        return candidates[i];
      }
    }
    return null;
  }, [anchorsTheAssistant]);

  // Calculate and update spacer height
  const updateSpacerHeight = useCallback(() => {
    if (!ref.current) return;

    // "top" is an explicit opt-in to pinning, so it carries the anchor on its
    // own rather than riding on the user-message flag.
    if ((autoScrollUserMessage || anchorsTheAssistant) && anchorRef.current) {
      const containerHeight = ref.current.clientHeight;
      const lastMessageHeight = anchorRef.current.offsetHeight;

      // Calculate the height of all elements after the anchor
      let afterMessagesHeight = 0;
      let currentElement = anchorRef.current.nextElementSibling;

      // Iterate through all siblings after the anchor
      while (currentElement && currentElement !== spacerRef.current) {
        afterMessagesHeight += (currentElement as HTMLElement).offsetHeight;
        currentElement = currentElement.nextElementSibling;
      }

      // Position the anchor at the top with some padding
      // Subtract both the message height and the height of any messages after it
      const newSpacerHeight =
        containerHeight - lastMessageHeight - afterMessagesHeight - 32;

      // Only set a positive spacer height
      if (spacerRef.current) {
        spacerRef.current.style.height = `${Math.max(0, newSpacerHeight)}px`;
      }

      if (anchorsTheAssistant) {
        // Only a new message moves the view — a resize or another token does
        // not — and the reply is never followed to the bottom.
        if (scrolledToRef.current !== anchorRef.current) {
          scrolledToRef.current = anchorRef.current;
          scrollToPosition();
        }
        return;
      }

      // Scroll to position the message at the top
      if (afterMessagesHeight === 0) {
        scrollToPosition();
      } else if (autoScrollAssistantMessage && autoScrollRef?.current) {
        ref.current.scrollTop = ref.current.scrollHeight;
      }
    } else if (
      autoScrollAssistantMessage &&
      autoScrollRef?.current &&
      !anchorsTheAssistant
    ) {
      ref.current.scrollTop = ref.current.scrollHeight;
    }
  }, [
    autoScrollUserMessage,
    autoScrollAssistantMessage,
    anchorsTheAssistant,
    autoScrollRef
  ]);

  // Find and set a ref to the anchor element
  useEffect(() => {
    if (!ref.current) return;

    if (messages.length === 0 && spacerRef.current) {
      spacerRef.current.style.height = `0px`;
      // A new chat detaches the anchor; keeping it would have a later resize
      // size the spacer around an element no longer in the document.
      anchorRef.current = null;
      scrolledToRef.current = null;
      return;
    }

    const anchor = findAnchor();
    if (anchor) {
      anchorRef.current = anchor;

      // Update spacer height when the anchor is found
      updateSpacerHeight();
    }
  }, [messages, findAnchor, updateSpacerHeight]);

  // Add window resize listener to update spacer height
  useEffect(() => {
    if (!autoScrollUserMessage && !anchorsTheAssistant) return;

    const handleResize = () => {
      updateSpacerHeight();
    };

    window.addEventListener('resize', handleResize);

    // Initial update
    updateSpacerHeight();

    return () => {
      window.removeEventListener('resize', handleResize);
    };
  }, [autoScrollUserMessage, anchorsTheAssistant, updateSpacerHeight]);

  // Check scroll position on mount
  useEffect(() => {
    if (!ref.current) return;

    setTimeout(() => {
      if (!ref.current) return;

      const { scrollTop, scrollHeight, clientHeight } = ref.current;
      const atBottom = scrollTop + clientHeight >= scrollHeight - 10;
      setShowScrollButton(!atBottom);
    }, 500);
  }, []);

  const checkScrollEnd = () => {
    if (!ref.current) return;

    const prevScrollTop = ref.current.scrollTop;

    setTimeout(() => {
      if (!ref.current) return;

      const currentScrollTop = ref.current.scrollTop;
      if (currentScrollTop === prevScrollTop) {
        setIsScrolling(false);

        const { scrollTop, scrollHeight, clientHeight } = ref.current;
        const atBottom = scrollTop + clientHeight >= scrollHeight - 10;
        setShowScrollButton(!atBottom);
      } else {
        checkScrollEnd();
      }
    }, 100);
  };

  const scrollToBottom = () => {
    if (!ref.current) return;

    setIsScrolling(true);
    ref.current.scrollTo({
      top: ref.current.scrollHeight,
      behavior: 'smooth'
    });

    if (autoScrollRef) {
      autoScrollRef.current = true;
    }

    setShowScrollButton(false);
    checkScrollEnd();
  };

  const scrollToPosition = () => {
    if (!ref.current || !anchorRef.current) return;

    setIsScrolling(true);
    // Scroll to position the anchor at the top with some padding
    const scrollPosition = anchorRef.current.offsetTop - 20;

    ref.current.scrollTo({
      top: scrollPosition,
      behavior: 'smooth'
    });

    setShowScrollButton(false);
    checkScrollEnd();
  };

  const handleScroll = () => {
    if (!ref.current || isScrolling) return;
    const { scrollTop, scrollHeight, clientHeight } = ref.current;
    const atBottom = scrollTop + clientHeight >= scrollHeight - 10;

    if (autoScrollRef) {
      autoScrollRef.current = atBottom;
    }

    setShowScrollButton(!atBottom);
  };

  return (
    <div className="relative flex flex-col flex-grow overflow-y-auto">
      <div
        ref={ref}
        className={cn('flex flex-col flex-grow overflow-y-auto', className)}
        onScroll={handleScroll}
      >
        {children}
        {/* Dynamic spacer to position the anchor message at the top */}
        <div ref={spacerRef} className="flex-shrink-0" />
      </div>

      {showScrollButton ? (
        <div className="absolute bottom-4 left-0 right-0 flex justify-center">
          <Button
            size="icon"
            variant="outline"
            className="rounded-full"
            onClick={scrollToBottom}
          >
            <ArrowDown className="size-4" />
          </Button>
        </div>
      ) : null}
    </div>
  );
}
