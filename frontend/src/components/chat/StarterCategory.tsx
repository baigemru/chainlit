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
  // Two to a row on a phone, where a grid column is what gives the tiles a
  // shared width; wider than that they sit on one wrapping line.
  tiles: 'flex flex-wrap gap-2 max-sm:grid max-sm:grid-cols-2',
  plates:
    'flex flex-col gap-3 sm:flex-row sm:flex-wrap [&>*]:sm:flex-1 [&>*]:sm:basis-56',
  rows: 'flex flex-col gap-1'
};

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
      <span className="text-sm font-medium">{category.label}</span>
    </>
  );

  const description = category.description ? (
    <p className="text-xs text-muted-foreground">{category.description}</p>
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
        className="group w-full"
        data-test={`starter-category-${category.label}`}
        open={device !== 'mobile'}
      >
        <summary className="flex cursor-pointer list-none items-center gap-2 py-1 [&::-webkit-details-marker]:hidden">
          {heading}
          <span className="text-xs text-muted-foreground">
            · {category.starters.length}
          </span>
          <ChevronRight className="h-4 w-4 text-muted-foreground transition-transform group-open:rotate-90" />
        </summary>
        {description}
        <div className="mt-2">{list}</div>
      </details>
    );
  }

  return (
    <section
      className="flex w-full flex-col gap-2"
      data-test={`starter-category-${category.label}`}
    >
      <div className="flex items-center gap-2">{heading}</div>
      {description}
      {list}
    </section>
  );
}
