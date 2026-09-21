import { IOAuthProviderDetail } from '@chainlit/react-client';

import LinkIcon from '@/components/LinkIcon';
import { useTranslation } from 'components/i18n/Translator';
import { Auth0 } from 'components/icons/Auth0';
import { Cognito } from 'components/icons/Cognito';
import { Descope } from 'components/icons/Descope';
import { GitHub } from 'components/icons/Github';
import { Gitlab } from 'components/icons/Gitlab';
import { Google } from 'components/icons/Google';
import { Okta } from 'components/icons/Okta';

import { Button } from './ui/button';

function capitalizeFirstLetter(string: string) {
  return string.charAt(0).toUpperCase() + string.slice(1);
}

function getProviderName(provider: string) {
  switch (provider) {
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
  mode?: 'signin' | 'register';
  icon?: Pick<IOAuthProviderDetail, 'iconUrl' | 'iconUrlLight' | 'iconUrlDark'>;
  /**
   * The button's text, verbatim, instead of the translated
   * "<verb> with <provider>". An identity-provider shortcut carries its own
   * label from the config -- the deployment named it, and no bundle here
   * knows which identity providers a broker fronts.
   */
  label?: string;
  onClick: () => void;
}

function getLabelKeys(mode: 'signin' | 'register') {
  // Fall back to the older key for translation bundles that predate the
  // signin/register split.
  return mode === 'register'
    ? ['auth.provider.register', 'auth.provider.continue']
    : ['auth.provider.signin', 'auth.provider.continue'];
}

const ProviderButton = ({
  provider,
  mode = 'signin',
  icon,
  label,
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
      {hasCustomIcon ? (
        <LinkIcon
          iconUrl={icon?.iconUrl}
          iconUrlLight={icon?.iconUrlLight}
          iconUrlDark={icon?.iconUrlDark}
          className="h-4 w-4"
        />
      ) : (
        renderProviderIcon(provider.toLowerCase())
      )}
      {label ??
        t(getLabelKeys(mode), {
          provider: getProviderName(provider)
        })}
    </Button>
  );
};

export { ProviderButton };
