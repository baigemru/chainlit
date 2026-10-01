import { cn } from '@/lib/utils';
import { ArrowLeft, X } from 'lucide-react';
import { type ReactNode, useEffect, useState } from 'react';

import {
  IElementSidebarSlot,
  useElementSidebar,
  useFreshSlots
} from '@chainlit/react-client';

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
        // One ring of its own for the keyboard, none for a click.
        'flex-none rounded-full p-1 text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary',
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
 *
 * Each tab is a pill, and the pill is the wrapper, not the trigger: the
 * "×" has to sit inside it and still be a sibling of the trigger (see
 * `CloseSlot`), so the trigger is drawn bare and the frame around both of
 * them carries the border. `components/ui/tabs` is left alone — the custom
 * elements' import map hands it to host code, which expects shadcn's
 * segmented control.
 *
 * The dot is "changed while you were on another tab" (`useFreshSlots`). It
 * is `aria-hidden` and the trigger says it in words instead, so a screen
 * reader is not told about a decoration it cannot see.
 *
 * One line that scrolls sideways, never a second row: a wrapped strip
 * pushes the panel's content down by a row every time a tab opens. The
 * scrollbar is hidden because the row is short enough to be dragged and a
 * bar under 22px of pills is taller than the gap it would sit in. `lead` is
 * whatever has to share the line -- the back arrow on the desktop -- and
 * sits outside the tablist, because it is not a tab.
 */
const Strip = ({
  slots,
  active,
  fresh,
  onClose,
  lead
}: {
  slots: IElementSidebarSlot[];
  active: string;
  fresh: ReadonlySet<string>;
  onClose: (id: string) => void;
  lead?: ReactNode;
}) => {
  if (slots.length < 2) return null;
  return (
    <div className="flex min-w-0 items-center gap-1.5">
      {lead}
      <TabsList
        id="side-view-tabs"
        className="flex h-auto min-w-0 flex-1 flex-nowrap justify-start gap-1.5 overflow-x-auto rounded-none bg-transparent p-0 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        {slots.map((slot) => {
          const isActive = slot.id === active;
          const isFresh = fresh.has(slot.id);
          return (
            <span
              key={slot.id}
              data-new={isFresh || undefined}
              className={cn(
                // The mockup's `.tab`: 11px on 3px by 9-10px, the accent at
                // 45% for the tab on screen.
                'inline-flex flex-none items-center whitespace-nowrap rounded-full border py-[3px] pl-2.5 text-[11px] leading-4 transition-colors',
                // Focus is drawn on the pill, round its own border, and only
                // for the keyboard. The trigger's shadcn ring sat *inside* the
                // pill, smaller than it, and doubled the active tab's outline
                // with a second red line.
                'has-[[role=tab]:focus-visible]:border-primary has-[[role=tab]:focus-visible]:ring-1 has-[[role=tab]:focus-visible]:ring-primary',
                slot.closable ? 'pr-1' : 'pr-2.5',
                isActive
                  ? 'border-primary/45 bg-background text-foreground'
                  : 'border-border text-muted-foreground hover:text-foreground'
              )}
            >
              <TabsTrigger
                value={slot.id}
                aria-description={isFresh ? 'updated' : undefined}
                className="rounded-full p-0 text-[11px] font-normal leading-4 focus-visible:ring-0 focus-visible:ring-offset-0 data-[state=active]:bg-transparent data-[state=active]:font-semibold data-[state=active]:shadow-none"
              >
                {/* Bounded, so one long product name cannot take the whole
                  line from the other tabs. */}
                <span className="max-w-[14rem] truncate">{slot.title}</span>
                {isFresh ? (
                  <span
                    aria-hidden
                    data-new-dot
                    className="ml-1.5 inline-block size-1.5 flex-none rounded-full bg-primary"
                  />
                ) : null}
              </TabsTrigger>
              <CloseSlot
                slot={slot}
                onClose={onClose}
                className="ml-0.5 p-0.5"
              />
            </span>
          );
        })}
      </TabsList>
    </div>
  );
};

export default function ElementSideView() {
  const { state, dispatch } = useElementSidebar();
  const fresh = useFreshSlots();
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
  const hasStrip = state.slots.length > 1;
  const hideButton = (
    <Button
      className={hasStrip ? '-ml-1.5 size-7 flex-none' : '-ml-2'}
      // Hides the panel; it does not destroy it. That difference is the
      // whole of this release, which is why the "×" beside it -- close the
      // slot -- is a separate control.
      onClick={() => dispatch({ op: 'hide' })}
      size="icon"
      variant={chromeless ? 'default' : 'ghost'}
      aria-label="Hide panel"
    >
      <ArrowLeft />
    </Button>
  );
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
                  {/* With a strip the active tab already says this, so the
                      title is for a screen reader only -- the dialog still
                      needs one. */}
                  <SheetTitle
                    id="side-view-title"
                    className={cn('flex-grow', hasStrip && 'sr-only')}
                  >
                    {current?.title ?? ''}
                  </SheetTitle>
                  {soleSlot ? (
                    <CloseSlot slot={soleSlot} onClose={close} />
                  ) : null}
                </div>
              </SheetHeader>
            ) : null}
            {/* `pr-8` keeps the last pill clear of the sheet's own "×",
                which is absolutely placed in this corner and is the way
                back on a phone. */}
            <div className="pr-8 empty:hidden">
              <Strip
                slots={state.slots}
                active={active}
                fresh={fresh}
                onClose={close}
              />
            </div>
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
                  'text-foreground px-6 flex items-center gap-1',
                  hasStrip ? 'py-3' : 'text-lg font-semibold py-4',
                  chromeless && 'absolute top-0 z-10 bg-transparent'
                )}
              >
                {hasStrip ? (
                  // No title over a strip: it would only repeat the active
                  // tab. The arrow moves into the strip's line instead.
                  <Strip
                    slots={state.slots}
                    active={active}
                    fresh={fresh}
                    onClose={close}
                    lead={hideButton}
                  />
                ) : (
                  <>
                    {hideButton}
                    {chromeless ? null : (
                      <>
                        <span className="flex-grow">
                          {current?.title ?? ''}
                        </span>
                        {soleSlot ? (
                          <CloseSlot slot={soleSlot} onClose={close} />
                        ) : null}
                      </>
                    )}
                  </>
                )}
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
