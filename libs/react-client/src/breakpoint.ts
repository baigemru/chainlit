/**
 * The width below which this is a phone.
 *
 * One constant, because two of them disagree the moment either moves. The
 * host's `useIsMobile` decides whether the element panel is drawn as a
 * 95%-wide sheet; the `sidebar.state` handler decides whether a reload has
 * to put that sheet away. Those have to be the same question, and they were
 * not while one read the viewport and the other read the `device` label the
 * hello carried — a `?device=pc` pin on a phone drew the sheet and then
 * refused to hide it.
 */
export const MOBILE_BREAKPOINT = 768;

/** Whether this viewport is a phone, right now. Safe outside a browser. */
export const isMobileViewport = (): boolean =>
  typeof window !== 'undefined' && window.innerWidth < MOBILE_BREAKPOINT;
