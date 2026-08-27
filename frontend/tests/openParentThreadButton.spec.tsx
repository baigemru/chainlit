import { render, screen } from '@testing-library/react';
import { RecoilRoot } from 'recoil';
import { describe, expect, it, vi } from 'vitest';

import OpenParentThreadButton from '@/components/chat/MessageComposer/OpenParentThreadButton';

const parentThreadId = vi.hoisted(() => ({
  current: 'parent-thread-1' as string | undefined
}));

vi.mock('@/hooks/useParentThread', () => ({
  useParentThreadId: () => parentThreadId.current
}));

const renderButton = (props: { disabled?: boolean } = {}) =>
  render(
    <RecoilRoot>
      <OpenParentThreadButton {...props} />
    </RecoilRoot>
  );

describe('OpenParentThreadButton', () => {
  it('is enabled by default', () => {
    renderButton();
    expect(screen.getByRole('button')).not.toBeDisabled();
  });

  it('is disabled while the composer is', () => {
    // Same gate as the settings button next to it: an in-flight generation,
    // a lost connection or an ask that owns the input must not be escaped
    // through the return button either.
    renderButton({ disabled: true });
    expect(screen.getByRole('button')).toBeDisabled();
  });

  it('renders nothing in a chat without a parent', () => {
    parentThreadId.current = undefined;
    try {
      renderButton({ disabled: true });
      expect(screen.queryByRole('button')).toBeNull();
    } finally {
      parentThreadId.current = 'parent-thread-1';
    }
  });
});
