import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog';

interface IframeModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  url: string;
}

export default function IframeModal({
  open,
  onOpenChange,
  title,
  url
}: IframeModalProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl h-[80vh] p-0 gap-0">
        <DialogHeader className="px-4 py-2 border-b">
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        <iframe
          src={url}
          className="w-full flex-1 rounded-b-lg"
          style={{ height: 'calc(80vh - 56px)' }}
          title={title}
        />
      </DialogContent>
    </Dialog>
  );
}