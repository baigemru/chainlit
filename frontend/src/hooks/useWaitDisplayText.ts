import { clampWaitIntervalMs, nextWaitIndex } from '@/lib/waitMessage';
import { useEffect, useRef, useState } from 'react';

import type { IStepWait } from '@chainlit/react-client';

/**
 * Rotation text to display for a message in wait mode, or undefined when
 * there is nothing to rotate (shimmer only over the persistent output).
 *
 * Display-only: the rotated text lives in local component state and is never
 * written back to the message store, so deactivation (or a reload) falls back
 * to the persistent output. Pass undefined while wait mode is inactive to
 * stop and clean up the timer. A new `wait` object identity restarts the
 * rotation from the first text.
 */
const useWaitDisplayText = (wait?: IStepWait): string | undefined => {
  const [index, setIndex] = useState(0);

  // Reset the rotation during render (not in the effect, which runs after
  // paint): switching to a new `wait` must never paint a frame of the new
  // list at the old index before snapping back to the first text.
  const prevWaitRef = useRef(wait);
  if (prevWaitRef.current !== wait) {
    prevWaitRef.current = wait;
    setIndex(0);
  }

  useEffect(() => {
    const texts = wait?.texts ?? [];
    if (!wait || texts.length <= 1) return;

    const intervalMs = clampWaitIntervalMs(wait.intervalMs);
    const loop = Boolean(wait.loop);
    let current = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const schedule = () => {
      timer = setTimeout(() => {
        const next = nextWaitIndex(current, texts.length, loop);
        // loop=false and the last text reached: hold, no further ticks.
        if (next === current) return;
        current = next;
        setIndex(next);
        schedule();
      }, intervalMs);
    };
    schedule();

    return () => clearTimeout(timer);
  }, [wait]);

  const texts = wait?.texts;
  if (!texts || !texts.length) return undefined;
  return texts[Math.min(index, texts.length - 1)];
};

export { useWaitDisplayText };
