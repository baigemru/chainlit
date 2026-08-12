import { cn } from '@/lib/utils';
import { Eye, EyeOff } from 'lucide-react';
import { Fragment, useEffect, useState } from 'react';
import { useForm } from 'react-hook-form';

import { ClientError, IOAuthProviderDetail } from '@chainlit/react-client';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import Translator, { useTranslation } from 'components/i18n/Translator';

import Alert from './Alert';
import { ProviderButton } from './ProviderButton';

interface Props {
  error?: string;
  providers: string[];
  providerDetails?: IOAuthProviderDetail[];
  callbackUrl: string;
  forgotPasswordUrl?: string;
  onPasswordSignIn?: (
    email: string,
    password: string,
    callbackUrl: string
  ) => Promise<any>;
  onOAuthSignIn?: (provider: string, callbackUrl: string) => Promise<any>;
  onOAuthSignUp?: (provider: string, callbackUrl: string) => Promise<any>;
  onOAuthVkSignIn?: (provider: string, callbackUrl: string) => Promise<any>;
}

interface FormValues {
  email: string;
  password: string;
}

export function LoginForm({
  providers,
  providerDetails,
  onPasswordSignIn,
  onOAuthSignIn,
  onOAuthSignUp,
  onOAuthVkSignIn,
  callbackUrl,
  forgotPasswordUrl,
  error
}: Props) {
  const [loading, setLoading] = useState(false);
  const [errorState, setErrorState] = useState(error);
  const [showPassword, setShowPassword] = useState(false);

  const { t } = useTranslation();

  const {
    register,
    handleSubmit,
    formState: { errors, touchedFields }
  } = useForm<FormValues>({
    defaultValues: {
      email: '',
      password: ''
    }
  });

  useEffect(() => {
    setErrorState(error);
  }, [error]);

  const onSubmit = async (data: FormValues) => {
    if (!onPasswordSignIn) return;

    setLoading(true);
    try {
      await onPasswordSignIn(data.email, data.password, callbackUrl);
    } catch (err) {
      if (err instanceof ClientError && err.detail) {
        setErrorState(err.detail);
      } else if (err instanceof Error) {
        setErrorState(err.message);
      }
    } finally {
      setLoading(false);
    }
  };

  const providerItems: IOAuthProviderDetail[] = providerDetails?.length
    ? providerDetails
    : providers.map((provider) => ({
        id: provider,
        loginEnabled: true,
        registrationEnabled: false
      }));

  const oAuthReady =
    onOAuthSignIn &&
    providerItems.some(
      (provider) =>
        provider.loginEnabled ||
        provider.registrationEnabled ||
        provider.vkEnabled
    );

  return (
    <form
      onSubmit={handleSubmit(onSubmit)}
      className={cn('flex flex-col gap-6')}
    >
      <div className="flex flex-col items-center gap-2 text-center">
        <h1 className="text-2xl font-bold">
          <Translator path="auth.login.title" />
        </h1>
      </div>

      {errorState && (
        <Alert variant="error">
          {t([
            `auth.login.errors.${errorState.toLowerCase()}`,
            `auth.login.errors.default`
          ])}
        </Alert>
      )}

      <div className="grid gap-6">
        {onPasswordSignIn && (
          <>
            <div className="grid gap-2">
              <Label htmlFor="email">
                <Translator path="auth.login.form.email.label" />
              </Label>
              <Input
                id="email"
                disabled={loading}
                autoFocus
                placeholder={t('auth.login.form.email.placeholder')}
                {...register('email', {
                  required: t('auth.login.form.email.required')
                })}
                className={cn(
                  touchedFields.email && errors.email && 'border-destructive'
                )}
              />
              {touchedFields.email && errors.email && (
                <p className="text-sm text-destructive">
                  {errors.email.message}
                </p>
              )}
            </div>

            <div className="grid gap-2">
              <div className="flex items-center justify-between">
                <Label htmlFor="password">
                  <Translator path="auth.login.form.password.label" />
                </Label>
                {forgotPasswordUrl ? (
                  <a
                    href={forgotPasswordUrl}
                    className="text-sm text-muted-foreground underline-offset-4 hover:underline"
                  >
                    <Translator path="auth.login.form.forgotPassword" />
                  </a>
                ) : null}
              </div>
              <div className="relative">
                <Input
                  id="password"
                  disabled={loading}
                  type={showPassword ? 'text' : 'password'}
                  {...register('password', {
                    required: t('auth.login.form.password.required')
                  })}
                  className={cn(
                    touchedFields.password &&
                      errors.password &&
                      'border-destructive'
                  )}
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="absolute right-2 top-1/2 -translate-y-1/2"
                  onClick={() => setShowPassword(!showPassword)}
                >
                  {showPassword ? (
                    <EyeOff className="h-4 w-4" />
                  ) : (
                    <Eye className="h-4 w-4" />
                  )}
                </Button>
              </div>
              {touchedFields.password && errors.password && (
                <p className="text-sm text-destructive">
                  {errors.password.message}
                </p>
              )}
            </div>

            <Button type="submit" className="w-full" disabled={loading}>
              <Translator path="auth.login.form.actions.signin" />
            </Button>
          </>
        )}

        {onPasswordSignIn && oAuthReady ? (
          <div className="relative text-center text-sm after:absolute after:inset-0 after:top-1/2 after:z-0 after:flex after:items-center after:border-t after:border-border">
            <span className="relative z-10 bg-background px-2 text-muted-foreground">
              <Translator path="auth.login.form.alternativeText.or" />
            </span>
          </div>
        ) : null}

        {oAuthReady ? (
          <div className="grid gap-2">
            {providerItems.map((provider) => (
              <Fragment key={`provider-${provider.id}`}>
                {provider.loginEnabled ? (
                  <ProviderButton
                    provider={provider.id}
                    icon={provider}
                    onClick={() => onOAuthSignIn?.(provider.id, callbackUrl)}
                  />
                ) : null}
                {provider.registrationEnabled ? (
                  <ProviderButton
                    provider={provider.id}
                    mode="register"
                    icon={provider}
                    onClick={() => onOAuthSignUp?.(provider.id, callbackUrl)}
                  />
                ) : null}
                {provider.vkEnabled ? (
                  <ProviderButton
                    provider={provider.id}
                    mode="vk"
                    icon={provider}
                    onClick={() => onOAuthVkSignIn?.(provider.id, callbackUrl)}
                  />
                ) : null}
              </Fragment>
            ))}
          </div>
        ) : null}
      </div>
    </form>
  );
}
