import { cn } from '@/lib/utils';
import { ArrowLeft, X } from 'lucide-react';
import { useEffect, useState } from 'react';

import { IElementSidebarSlot, useElementSidebar } from '@chainlit/react-client';

import { Card, CardContent } from '@/components/ui/card';
import { ResizableHandle, ResizablePanel } from '@/components/ui/resizable';
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle
} from '@/components/ui/sheet';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';

import { useIsMobile } from '@/hooks/use-mobile';

import { Element } from './Elements';
import { Button } from './ui/button';

/**
 * The element panel, drawn from the session's own model.
 *
 * Two things here are load-bearing and easy to undo.
 *
 * **Every slot is mounted, always.** `TabsContent forceMount` keeps the
 * inactive ones in the tree with `hidden` on them, so switching tabs does not
 * remount a custom element and lose whatever the user had typed into it. That
 * is what the old `key` field was really trying to buy, by forbidding updates
 * instead.
 *
 * **The `Tabs` tree exists whatever the slot count is.** A one-slot panel
 * simply has no tab strip. Rendering a lone slot outside `Tabs` and moving it
 * in when a second appeared would remount it on the way — the exact thing the
 * paragraph above is about.
 */

/**
 * The slot bodies, kept mounted, with only the active one visible.
 *
 * `hidden` is stated on the element rather than left to Radix. With
 * `forceMount` Radix's own `present` is true for every slot, so its
 * `hidden={!present}` never fires and the inactive ones are only ever hidden
 * by a stylesheet — which is exactly the kind of invisible dependency a test
 * cannot see and a missing Tailwind utility would silently break.
 */
const Bodies = ({
  slots,
  active
}: {
  slots: IElementSidebarSlot[];
  active: string;
}) =>
  slots.map((slot) => (
    <TabsContent
      key={slot.id}
      value={slot.id}
      forceMount
      hidden={slot.id !== active}
      className={cn(
        'mt-0 flex-col flex-grow gap-4',
        slot.id === active ? 'flex' : 'hidden'
      )}
      data-slot-id={slot.id}
    >
      {slot.elements.map((element) => (
        <Element key={element.id} element={element} />
      ))}
    </TabsContent>
  ));

/**
 * "Close this slot", wherever the slot is addressed from.
 *
 * Its own button, never nested inside a `TabsTrigger`: interactive content
 * inside a tab is invalid, and the click would have to be fought off the
 * trigger with `preventDefault` — which then also swallows the focus the
 * trigger activates on. A sibling has none of that to explain.
 *
 * Distinct from the back arrow and the sheet's own close, which *hide*. That
 * difference is the whole of this release, so both have to be reachable.
 */
const CloseSlot = ({
  slot,
  onClose,
  className
}: {
  slot: IElementSidebarSlot;
  onClose: (id: string) => void;
  className?: string;
}) =>
  slot.closable ? (
    <button
      type="button"
      data-close-slot={slot.id}
      aria-label={`Close ${slot.title}`}
      onClick={() => onClose(slot.id)}
      className={cn(
        'flex-none rounded-sm p-1 text-muted-foreground hover:text-foreground',
        className
      )}
    >
      <X className="size-3" />
    </button>
  ) : null;

/**
 * The tab strip, whenever there is more than one slot — on both layouts, and
 * whatever the active slot is. A canvas slot suppresses *chrome*, not
 * navigation: hiding the strip because the tab you are on happens to be a
 * canvas leaves the panel one-way, with hiding it and reopening it the only
 * way back to the others.
 */
const Strip = ({
  slots,
  onClose
}: {
  slots: IElementSidebarSlot[];
  onClose: (id: string) => void;
}) => {
  if (slots.length < 2) return null;
  return (
    <TabsList id="side-view-tabs" className="h-auto flex-wrap justify-start">
      {slots.map((slot) => (
        <span key={slot.id} className="inline-flex items-center">
          <TabsTrigger value={slot.id}>{slot.title}</TabsTrigger>
          <CloseSlot slot={slot} onClose={onClose} className="-ml-1" />
        </span>
      ))}
    </TabsList>
  );
};

export default function ElementSideView() {
  const { state, dispatch } = useElementSidebar();
  const isMobile = useIsMobile();
  const [isVisible, setIsVisible] = useState(false);

  const active = state.active ?? state.slots[0]?.id ?? '';
  const current = state.slots.find((slot) => slot.id === active);
  // A property of the slot now. It used to be `title === 'canvas'`, which
  // made a panel titled "canvas" in any language a different widget.
  const isCanvas = !!current?.canvas;
  // A canvas gives up its header — but only while it is the whole panel.
  // With a second slot beside it the title bar is also where the tab strip
  // and the close buttons live, and dropping it strands the user on the
  // canvas.
  const chromeless = isCanvas && state.slots.length === 1;
  // With one slot there is no strip to carry a "×", so the header does.
  const soleSlot = state.slots.length === 1 ? current : undefined;

  useEffect(() => {
    if (state.visible) {
      // Delay setting visibility to trigger animation
      requestAnimationFrame(() => {
        setIsVisible(true);
      });
    } else {
      setIsVisible(false);
    }
  }, [state.visible]);

  if (!state.visible) return null;

  const close = (id: string) => dispatch({ op: 'close', slot: id });
  const onValueChange = (slot: string) => dispatch({ op: 'activate', slot });

  if (isMobile) {
    return (
      <Sheet open onOpenChange={(open) => !open && dispatch({ op: 'hide' })}>
        {/* Almost the whole screen: the sheet's default three quarters left a
            product card squeezed against a 175px image on a phone. */}
        <SheetContent
          className={cn(
            'md:hidden flex flex-col w-[95vw] sm:max-w-[95vw]',
            chromeless && 'p-0'
          )}
        >
          <Tabs
            value={active}
            onValueChange={onValueChange}
            className="flex flex-col flex-grow overflow-hidden"
          >
            {!chromeless ? (
              <SheetHeader>
                <div className="flex items-center gap-1">
                  <SheetTitle id="side-view-title" className="flex-grow">
                    {current?.title ?? ''}
                  </SheetTitle>
                  {soleSlot ? (
                    <CloseSlot slot={soleSlot} onClose={close} />
                  ) : null}
                </div>
              </SheetHeader>
            ) : null}
            <Strip slots={state.slots} onClose={close} />
            <div
              id="side-view-content"
              className={cn(
                'overflow-auto flex-grow flex flex-col',
                isCanvas ? 'p-0' : 'gap-4 mt-4'
              )}
            >
              <Bodies slots={state.slots} active={active} />
            </div>
          </Tabs>
        </SheetContent>
      </Sheet>
    );
  }

  return (
    <>
      <ResizableHandle className="sm:hidden md:block bg-transparent" />
      <ResizablePanel
        minSize={isCanvas ? 30 : 10}
        defaultSize={50}
        className={`md:flex flex-col flex-grow sm:hidden transform transition-transform duration-300 ease-in-out ${
          isVisible ? 'translate-x-0' : 'translate-x-full'
        }`}
      >
        <aside className="relative flex-grow overflow-auto mr-4 mb-4">
          <Card className="overflow-auto h-full relative flex flex-col">
            <Tabs
              value={active}
              onValueChange={onValueChange}
              className="flex flex-col flex-grow"
            >
              <div
                id="side-view-title"
                className={cn(
                  'text-lg font-semibold text-foreground px-6 py-4 flex items-center gap-1',
                  chromeless && 'absolute top-0 z-10 bg-transparent'
                )}
              >
                <Button
                  className="-ml-2"
                  // Hides the panel; it does not destroy it. That difference
                  // is the whole of this release, which is why the "×" beside
                  // it -- close the slot -- is a separate control.
                  onClick={() => dispatch({ op: 'hide' })}
                  size="icon"
                  variant={chromeless ? 'default' : 'ghost'}
                >
                  <ArrowLeft />
                </Button>
                {chromeless ? null : (
                  <>
                    <span className="flex-grow">{current?.title ?? ''}</span>
                    {soleSlot ? (
                      <CloseSlot slot={soleSlot} onClose={close} />
                    ) : null}
                  </>
                )}
              </div>
              <div className="px-6 empty:hidden">
                <Strip slots={state.slots} onClose={close} />
              </div>
              <CardContent
                id="side-view-content"
                className={cn(
                  'flex flex-col flex-grow',
                  isCanvas ? 'p-0' : 'gap-4'
                )}
              >
                <Bodies slots={state.slots} active={active} />
              </CardContent>
            </Tabs>
          </Card>
        </aside>
      </ResizablePanel>
    </>
  );
}
