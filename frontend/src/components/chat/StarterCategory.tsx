import { cn } from '@/lib/utils';
import { ChevronRight } from 'lucide-react';
import { useContext } from 'react';

import {
  ChainlitContext,
  IStarterCategory,
  type StarterLayout
} from '@chainlit/react-client';

import type { SessionDevice } from '@/hooks/use-mobile';

import Starter, { starterIconSrc } from './Starter';

/**
 * A category is a section of the welcome screen, not a filter over one. It
 * was a pill that revealed its starters when pressed; every category is now
 * on screen at once, in the order the server sent them, because a list of
 * offers the user has to click through to read is a list they do not read.
 */
interface Props {
  category: IStarterCategory;
  device: SessionDevice;
}

/**
 * An unknown layout is a newer backend talking, not a broken one: fall back
 * to the shape every starter has always had rather than drawing nothing.
 */
const resolveLayout = (layout: string | undefined): StarterLayout =>
  layout === 'plates' || layout === 'rows' ? layout : 'tiles';

const listClasses: Record<StarterLayout, string> = {
  // A grid at every width, phone included: a wrapping flex line sized every
  // tile to its own label, so two offers of equal standing came out one wide
  // and one narrow. A column is a promise that they weigh the same.
  tiles: 'grid grid-cols-2 gap-2',
  plates:
    'flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-stretch [&>*]:sm:flex-1 [&>*]:sm:basis-56',
  rows: 'flex flex-col gap-1'
};

/**
 * The section's own subtitle, set against its heading: upper case, spaced and
 * monospaced, so that a line the application writes to qualify a heading reads
 * as an aside to it and never competes with it.
 */
const descriptionClasses =
  'min-w-0 font-mono text-xs uppercase tracking-wider text-muted-foreground';

export default function StarterCategory({ category, device }: Props) {
  const apiClient = useContext(ChainlitContext);
  const layout = resolveLayout(category.layout);

  const heading = (
    <>
      {category.icon ? (
        <img
          className="h-4 w-4 shrink-0"
          src={starterIconSrc(category.icon, apiClient)}
          alt=""
        />
      ) : null}
      <span className="shrink-0 text-sm font-semibold">{category.label}</span>
    </>
  );

  // Beside the heading and not under it, which is why it is a function: the
  // folded section wants the same line back on a phone, where a subtitle in
  // the summary would push the fold affordance onto a second row.
  const description = (className?: string) =>
    category.description ? (
      <span className={cn(descriptionClasses, className)}>
        {category.description}
      </span>
    ) : null;

  const list = (
    <div className={cn(listClasses[layout], 'w-full')}>
      {/* By position, as the flat list keys its own. The label is not an
          identity: two errands in one section may share one — the same
          question put to two profiles — and React would reconcile the pair
          into a single button. The list is a static array from the server,
          so position is the only identity there is. */}
      {category.starters.map((starter, index) => (
        <Starter key={index} starter={starter} layout={layout} />
      ))}
    </div>
  );

  if (category.collapsible) {
    return (
      // `<details>` and not a Radix collapsible: the Radix wrappers were
      // cleaned out of this app on 15.09, and a section that folds is
      // exactly what the element is for — keyboard, screen reader and
      // find-in-page included, for no dependency at all.
      //
      // `open` is read from the device key rather than `useIsMobile`, which
      // reports `false` until its effect lands: on a phone that is a section
      // painted open and snapped shut a frame later.
      <details
        className="group w-full border-t border-border pt-4"
        data-test={`starter-category-${category.label}`}
        open={device !== 'mobile'}
      >
        <summary className="flex cursor-pointer list-none flex-wrap items-center gap-x-3 gap-y-1 py-1 [&::-webkit-details-marker]:hidden">
          {heading}
          {description('max-sm:hidden')}
          {/* How much is folded away, where anything is: the section arrives
              open on a desktop, and a count of what is already on screen is
              noise beside the heading. */}
          <span className="text-xs text-muted-foreground sm:hidden">
            · {category.starters.length}
          </span>
          <ChevronRight className="ml-auto h-4 w-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-90" />
        </summary>
        <div className="mt-2">{list}</div>
      </details>
    );
  }

  return (
    // The rule above the heading is the only thing separating one tier from
    // the next: the sections are of different densities on purpose, and
    // without a line between them a row of tiles reads as the tail of the
    // section above it.
    <section
      className="flex w-full flex-col gap-2 border-t border-border pt-4"
      data-test={`starter-category-${category.label}`}
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        {heading}
        {description()}
      </div>
      {list}
    </section>
  );
}
