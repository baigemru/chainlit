import { cn } from '@/lib/utils';
import capitalize from 'lodash/capitalize';

import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar';
import { useTranslation } from 'components/i18n/Translator';

interface Props {
  /** What the user is called; its first letter is the fallback. */
  displayName: string;
  image?: string;
  /**
   * How many things on the account page the user has not seen. A dot is
   * drawn for anything above zero and nothing at all for `0` or `undefined`
   * — the first is the application saying there is nothing, the second is
   * an application that registered no badge hook, and neither is news.
   */
  unseen?: number;
  className?: string;
}

/**
 * The user's face, with the account badge on it.
 *
 * Two places draw it — the header's avatar and the row at the foot of the
 * left sidebar — and they must not drift: the badge is the same news in
 * both, and a dot that appears on one and not the other teaches the user
 * that one of them is lying.
 *
 * The wrapper owns the positioning rather than borrowing its parent's:
 * the header hangs the avatar off a `relative` button and the sidebar off a
 * `SidebarMenuButton`, and a dot that needed a `relative` ancestor would be
 * a requirement on every future caller.
 */
export const UserAvatar = ({
  displayName,
  image,
  unseen,
  className
}: Props) => {
  const { t } = useTranslation();

  return (
    <span className="relative inline-flex shrink-0">
      <Avatar className={cn('h-8 w-8', className)}>
        {/* `GET /user` omits an empty `metadata` (msgspec
            `omit_defaults`), so a user with nothing in it arrives
            without the key at all. */}
        <AvatarImage src={image} alt="user image" />
        <AvatarFallback className="bg-primary text-primary-foreground font-semibold">
          {capitalize(displayName[0])}
        </AvatarFallback>
      </Avatar>
      {/* The menu is closed most of the time, so the count inside it
          cannot be what tells the user there is something to see. */}
      {(unseen ?? 0) > 0 ? (
        <span
          aria-label={t('account.badge.aria')}
          className="absolute -top-0.5 -right-0.5 size-2.5 rounded-full bg-destructive ring-2 ring-background"
        />
      ) : null}
    </span>
  );
};

export default UserAvatar;
