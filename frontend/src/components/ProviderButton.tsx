import { IOAuthProviderDetail } from '@chainlit/react-client';

import LinkIcon from '@/components/LinkIcon';
import { useTranslation } from 'components/i18n/Translator';
import { Auth0 } from 'components/icons/Auth0';
import { Cognito } from 'components/icons/Cognito';
import { Descope } from 'components/icons/Descope';
import { GitHub } from 'components/icons/Github';
import { Gitlab } from 'components/icons/Gitlab';
import { Google } from 'components/icons/Google';
import { Microsoft } from 'components/icons/Microsoft';
import { Okta } from 'components/icons/Okta';
import { VK } from 'components/icons/VK';
import { Yandex } from 'components/icons/Yandex';

import { Button } from './ui/button';

function capitalizeFirstLetter(string: string) {
  return string.charAt(0).toUpperCase() + string.slice(1);
}

function getProviderName(provider: string) {
  switch (provider) {
    case 'azure-ad':
    case 'azure-ad-hybrid':
      return 'Microsoft';
    case 'github':
      return 'GitHub';
    case 'okta':
      return 'Okta';
    case 'descope':
      return 'Descope';
    case 'aws-cognito':
      return 'Cognito';
    default:
      return capitalizeFirstLetter(provider);
  }
}

function renderProviderIcon(provider: string) {
  switch (provider) {
    case 'google':
      return <Google />;
    case 'github':
      return <GitHub />;
    case 'azure-ad':
    case 'azure-ad-hybrid':
      return <Microsoft />;
    case 'okta':
      return <Okta />;
    case 'auth0':
      return <Auth0 />;
    case 'descope':
      return <Descope />;
    case 'aws-cognito':
      return <Cognito />;
    case 'gitlab':
      return <Gitlab />;
    default:
      return null;
  }
}

interface ProviderButtonProps {
  provider: string;
  mode?: 'signin' | 'register' | 'vk' | 'yandex';
  icon?: Pick<IOAuthProviderDetail, 'iconUrl' | 'iconUrlLight' | 'iconUrlDark'>;
  onClick: () => void;
}

function getLabelKeys(mode: 'signin' | 'register' | 'vk' | 'yandex') {
  // Fall back to older keys for translation bundles that predate the newer
  // ones (signin/register split, then the VK/Yandex shortcut buttons).
  switch (mode) {
    case 'register':
      return ['auth.provider.register', 'auth.provider.continue'];
    case 'vk':
      return ['auth.provider.vk', 'auth.provider.signin'];
    case 'yandex':
      return ['auth.provider.yandex', 'auth.provider.signin'];
    default:
      return ['auth.provider.signin', 'auth.provider.continue'];
  }
}

const ProviderButton = ({
  provider,
  mode = 'signin',
  icon,
  onClick
}: ProviderButtonProps): JSX.Element => {
  const { t } = useTranslation();
  const hasCustomIcon = !!(
    icon?.iconUrl ||
    icon?.iconUrlLight ||
    icon?.iconUrlDark
  );
  return (
    <Button type="button" variant="outline" onClick={onClick}>
      {mode === 'vk' ? (
        <VK />
      ) : mode === 'yandex' ? (
        <Yandex />
      ) : hasCustomIcon ? (
        <LinkIcon
          iconUrl={icon?.iconUrl}
          iconUrlLight={icon?.iconUrlLight}
          iconUrlDark={icon?.iconUrlDark}
          className="h-4 w-4"
        />
      ) : (
        renderProviderIcon(provider.toLowerCase())
      )}
      {t(getLabelKeys(mode), {
        provider: getProviderName(provider)
      })}
    </Button>
  );
};

export { ProviderButton };
