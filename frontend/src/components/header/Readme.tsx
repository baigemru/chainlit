import { BookOpen } from 'lucide-react';
import { useState } from 'react';

import { useConfig } from '@chainlit/react-client';

import { Markdown } from '@/components/Markdown';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog';
import { DropdownMenuItem } from '@/components/ui/dropdown-menu';
import { Translator } from 'components/i18n';

import { useLayoutMaxWidth } from 'hooks/useLayoutMaxWidth';

interface Props {
  /** Render as a row of the header's overflow menu instead of a button. */
  collapsed?: boolean;
}

export default function ReadmeButton({ collapsed }: Props) {
  const { config } = useConfig();
  const layoutMaxWidth = useLayoutMaxWidth();
  const [open, setOpen] = useState(false);

  if (!config?.markdown) {
    return null;
  }

  return (
    <>
      {collapsed ? (
        <DropdownMenuItem
          id="readme-button"
          // Selecting a row closes the menu, and the dialog is mounted here:
          // it would be gone before it painted.
          onSelect={(e) => e.preventDefault()}
          onClick={() => setOpen(true)}
        >
          <Translator path="navigation.header.readme" />
          <BookOpen className="ml-auto size-4" />
        </DropdownMenuItem>
      ) : (
        <Button
          id="readme-button"
          variant="ghost"
          onClick={() => setOpen(true)}
        >
          <Translator path="navigation.header.readme" />
        </Button>
      )}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="flex flex-col h-screen w-screen max-w-screen max-h-screen border-none !rounded-none overflow-y-auto">
          <div
            className="mx-auto flex flex-col flex-grow gap-6"
            style={{
              maxWidth: layoutMaxWidth
            }}
          >
            <DialogHeader>
              <DialogTitle>
                <Translator path="navigation.header.readme" />
              </DialogTitle>
            </DialogHeader>
            <Markdown
              className="flex flex-col flex-grow overflow-y-auto"
              allowHtml={config?.features?.unsafe_allow_html}
              latex={config?.features?.latex}
              renderMarkdown={true}
            >
              {config.markdown}
            </Markdown>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
