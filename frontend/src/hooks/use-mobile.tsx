import * as React from 'react';

import type { ChatProfile, DeviceKey } from '@chainlit/react-client';

const MOBILE_BREAKPOINT = 768;

const DEVICE_OVERRIDE_KEY = 'chainlit_device_override';

/** The device class an offer is filtered against. A tablet counts as `pc`. */
export type SessionDevice = 'mobile' | 'pc';

export function useIsMobile() {
  const [isMobile, setIsMobile] = React.useState<boolean | undefined>(
    undefined
  );

  React.useEffect(() => {
    const mql = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT - 1}px)`);
    const onChange = () => {
      setIsMobile(window.innerWidth < MOBILE_BREAKPOINT);
    };
    mql.addEventListener('change', onChange);
    setIsMobile(window.innerWidth < MOBILE_BREAKPOINT);
    return () => mql.removeEventListener('change', onChange);
  }, []);

  return !!isMobile;
}

const parseDevice = (value: string | null): SessionDevice | undefined =>
  value === 'mobile' || value === 'pc' ? value : undefined;

/**
 * `?device=` outranks the viewport, and outlives the URL that carried it:
 * the first SPA navigation drops the query, so the answer is parked in
 * sessionStorage — long enough for the tab, gone when it closes.
 *
 * Every touch of sessionStorage is guarded: an embedded webview can throw on
 * mere access, and an override nobody asked for is not worth a blank screen.
 */
const readOverride = (): SessionDevice | undefined => {
  const fromUrl = parseDevice(
    new URLSearchParams(window.location.search).get('device')
  );
  if (fromUrl) {
    try {
      window.sessionStorage.setItem(DEVICE_OVERRIDE_KEY, fromUrl);
    } catch {
      // No storage: the override still holds for as long as the query does.
    }
    return fromUrl;
  }
  try {
    return parseDevice(window.sessionStorage.getItem(DEVICE_OVERRIDE_KEY));
  } catch {
    return undefined;
  }
};

/**
 * The device class, read synchronously. Used where there is no render to
 * subscribe to — a click handler that has to decide right now.
 */
export function getDeviceKey(): SessionDevice {
  return (
    readOverride() ?? (window.innerWidth < MOBILE_BREAKPOINT ? 'mobile' : 'pc')
  );
}

/**
 * The device class for the current render, correct on the very first one:
 * `useIsMobile` reports `false` until its effect lands, and the default
 * profile is chosen in that window — a phone would be handed the desktop hub
 * and, `chatProfile` now set, never revisit the choice.
 */
export function useDeviceKey(): SessionDevice {
  const [device, setDevice] = React.useState<SessionDevice>(getDeviceKey);

  React.useEffect(() => {
    // A pinned device does not listen to the viewport; that is the point.
    if (readOverride()) return;

    const mql = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT - 1}px)`);
    const onChange = () => setDevice(getDeviceKey());
    mql.addEventListener('change', onChange);
    onChange();
    return () => mql.removeEventListener('change', onChange);
  }, []);

  return device;
}

/** Whether a labelled offer is meant for this device. */
export function matchesDevice(
  item: DeviceKey | undefined,
  device: SessionDevice
): boolean {
  const label = item as string | undefined;
  if (!label || label === 'all') return true;
  if (label === 'mobile' || label === 'pc') return label === device;
  // A label from a newer backend is not a reason to hide a button: showing
  // one offer too many beats swallowing the only one there was.
  return true;
}

/**
 * The one rule for which profile a fresh chat opens in, shared by the app's
 * boot effect, the profile selector and the new-chat button.
 *
 * The last fallback is deliberate: without a profile `App` never opens the
 * socket (`chatProfileOk`), so an entirely device-less config must still
 * yield a name.
 */
export function pickDefaultProfile(
  profiles: ChatProfile[],
  device: SessionDevice
): string | undefined {
  const visible = profiles.filter((profile) =>
    matchesDevice(profile.device, device)
  );
  const chosen =
    visible.find((profile) => profile.default) ?? visible[0] ?? profiles[0];
  return chosen?.name;
}
