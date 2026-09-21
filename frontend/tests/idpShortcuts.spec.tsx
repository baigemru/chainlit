/**
 * The login page's identity-provider shortcuts.
 *
 * The page used to carry two of these by name, VK and Yandex, as identical
 * branches. What is left is a list: the server says which shortcuts a
 * provider offers and what to write on each button, and the client draws
 * them. It never sees the alias the broker is told.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { IOAuthProviderDetail } from '@chainlit/react-client';

import { LoginForm } from '@/components/LoginForm';

vi.mock('components/i18n/Translator', () => ({
  default: ({ path }: { path: string }) => <span>{path}</span>,
  useTranslation: () => ({ t: (keys: string | string[]) => String(keys) })
}));

const keycloak = (
  shortcuts: IOAuthProviderDetail['idpShortcuts']
): IOAuthProviderDetail => ({
  id: 'keycloak',
  loginEnabled: false,
  registrationEnabled: false,
  idpShortcuts: shortcuts
});

describe('identity-provider shortcuts on the login page', () => {
  it('draws one button per shortcut, labelled as the config named it', () => {
    render(
      <LoginForm
        callbackUrl="/"
        providers={['keycloak']}
        providerDetails={[
          keycloak([
            { id: 'vk', label: 'Войти через VK' },
            { id: 'yandex', label: 'Войти через Яндекс' }
          ])
        ]}
        onOAuthSignIn={vi.fn()}
        onOAuthIdpSignIn={vi.fn()}
      />
    );

    expect(screen.getByText('Войти через VK')).toBeInTheDocument();
    expect(screen.getByText('Войти через Яндекс')).toBeInTheDocument();
  });

  it('asks for the shortcut by id, never by alias', () => {
    const onOAuthIdpSignIn = vi.fn();
    render(
      <LoginForm
        callbackUrl="/next"
        providers={['keycloak']}
        providerDetails={[keycloak([{ id: 'vk', label: 'VK' }])]}
        onOAuthSignIn={vi.fn()}
        onOAuthIdpSignIn={onOAuthIdpSignIn}
      />
    );

    fireEvent.click(screen.getByText('VK'));

    expect(onOAuthIdpSignIn).toHaveBeenCalledWith('keycloak', 'vk', '/next');
  });

  it('draws nothing for a provider that brokers nothing', () => {
    const { container } = render(
      <LoginForm
        callbackUrl="/"
        providers={['github']}
        providerDetails={[
          {
            id: 'github',
            loginEnabled: false,
            registrationEnabled: false,
            idpShortcuts: []
          }
        ]}
        onOAuthSignIn={vi.fn()}
        onOAuthIdpSignIn={vi.fn()}
      />
    );

    expect(container.querySelectorAll('button')).toHaveLength(0);
  });
});
