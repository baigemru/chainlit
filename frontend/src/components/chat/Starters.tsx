import { cn } from '@/lib/utils';
import { useMemo } from 'react';

import { useChatSession, useConfig } from '@chainlit/react-client';

import { matchesDevice, useDeviceKey } from '@/hooks/use-mobile';

import Starter from './Starter';
import StarterCategory from './StarterCategory';

interface Props {
  className?: string;
}

export default function Starters({ className }: Props) {
  const { chatProfile } = useChatSession();
  const { config } = useConfig();
  const device = useDeviceKey();

  const starters = useMemo(() => {
    if (chatProfile) {
      const selectedChatProfile = config?.chatProfiles.find(
        (profile) => profile.name === chatProfile
      );
      if (selectedChatProfile?.starters) {
        return selectedChatProfile.starters;
      }
    }
    return config?.starters;
  }, [config, chatProfile]);

  const visibleStarters = useMemo(
    () => starters?.filter((starter) => matchesDevice(starter.device, device)),
    [starters, device]
  );

  // A category whose every starter belongs to the other device is an empty
  // promise: it opens onto nothing, so it is not offered at all.
  const starterCategories = useMemo(
    () =>
      config?.starterCategories
        ?.map((category) => ({
          ...category,
          starters: category.starters.filter((starter) =>
            matchesDevice(starter.device, device)
          )
        }))
        .filter((category) => category.starters.length),
    [config, device]
  );

  // Categories are the welcome screen's tiers, top to bottom in the order the
  // server sent them. Nothing here chooses between them: the server decides
  // what is offered and in which density, and every section is on screen.
  if (starterCategories?.length) {
    return (
      <div
        id="starters"
        className={cn('flex w-full flex-col gap-6', className)}
      >
        {starterCategories.map((category) => (
          <StarterCategory
            key={category.label}
            category={category}
            device={device}
          />
        ))}
      </div>
    );
  }

  if (!visibleStarters?.length) return null;

  return (
    <div
      id="starters"
      className={cn('flex gap-2 justify-center flex-wrap', className)}
    >
      {visibleStarters.map((starter, i) => (
        <Starter key={i} starter={starter} />
      ))}
    </div>
  );
}
