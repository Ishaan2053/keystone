import * as React from 'react';
import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';
const buttonVariants = cva('button', {
  variants: {
    variant: { default: 'button-primary', secondary: 'button-secondary', ghost: 'button-ghost' },
  },
  defaultVariants: { variant: 'secondary' },
});
export function Button({
  className,
  variant,
  asChild = false,
  ...props
}: React.ComponentProps<'button'> & VariantProps<typeof buttonVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot : 'button';
  return (
    <Comp data-slot="button" className={cn(buttonVariants({ variant, className }))} {...props} />
  );
}
