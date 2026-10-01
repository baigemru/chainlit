import { cn } from '@/lib/utils';
import { Slot } from '@radix-ui/react-slot';
import { type VariantProps, cva } from 'class-variance-authority';
import * as React from 'react';

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium ring-offset-background transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        default: 'bg-primary text-primary-foreground hover:bg-primary/90',
        destructive:
          'bg-destructive text-destructive-foreground hover:bg-destructive/90',
        outline:
          'border border-input bg-background hover:bg-accent hover:text-accent-foreground',
        secondary:
          'bg-secondary text-secondary-foreground hover:bg-secondary/80',
        ghost: 'hover:bg-accent hover:text-accent-foreground',
        link: 'text-primary underline-offset-4 hover:underline'
      },
      size: {
        default: 'h-10 px-4 py-2',
        sm: 'h-9 rounded-md px-3',
        lg: 'h-11 rounded-md px-8',
        icon: 'h-9 w-9',
        // The two sizes below are the ones a line of text inside a card or
        // under a message wants. shadcn's own steps are boxes of fixed height
        // (h-9, h-10) with the text floating in them; these are sized by the
        // text -- `h-auto`, a 16px line, padding round it -- so the label
        // sits in the button the way it does in the mockup's `.b` and
        // `.b-xs`. Added beside the shadcn steps, never instead of them:
        // host elements get this module through the custom-element import
        // map and may use any of the old ones.
        //
        // `compact` is the mockup's `.b`: 12px on 5px by 11px, radius 8px.
        // Not `xs`-something, because it is not a step below `sm` on
        // shadcn's scale -- it is a different kind of size, and a t-shirt
        // name would promise an ordering it does not have.
        compact:
          'h-auto gap-1.5 rounded-lg px-[11px] py-[5px] text-xs leading-4 [&_svg]:size-3.5',
        // `xs` is `.b-xs`: 11px on 3px by 8px, radius 7px -- the name the
        // mockup gives it, and shadcn's for its smallest button.
        xs: 'h-auto gap-1 rounded-[7px] px-2 py-[3px] text-[11px] leading-4 [&_svg]:size-3'
      }
    },
    // At the text-sized steps the outline's `border-input` is the colour of
    // the surface it sits on, and a secondary button reads as loose text.
    // The mockup's secondary has a line a step stronger than the fill; the
    // accented one is a step heavier. The shadcn sizes keep shadcn's look.
    compoundVariants: [
      {
        variant: 'outline',
        size: ['compact', 'xs'],
        className: 'border-muted-foreground/40'
      },
      {
        variant: 'default',
        size: ['compact', 'xs'],
        className: 'font-semibold'
      }
    ],
    defaultVariants: {
      variant: 'default',
      size: 'default'
    }
  }
);

export interface ButtonProps
  extends
    React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : 'button';
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    );
  }
);
Button.displayName = 'Button';

export { Button, buttonVariants };
