'use client';
import * as React from 'react';
import * as SelectPrimitive from '@radix-ui/react-select';
import { Check, ChevronDown } from 'lucide-react';
export const Select = SelectPrimitive.Root;
export const SelectValue = SelectPrimitive.Value;
export function SelectTrigger({ children, ...props }: React.ComponentProps<typeof SelectPrimitive.Trigger>) { return <SelectPrimitive.Trigger className="select-trigger" {...props}>{children}<SelectPrimitive.Icon asChild><ChevronDown /></SelectPrimitive.Icon></SelectPrimitive.Trigger>; }
export function SelectContent({ children, ...props }: React.ComponentProps<typeof SelectPrimitive.Content>) { return <SelectPrimitive.Portal><SelectPrimitive.Content className="select-content" position="popper" sideOffset={6} {...props}><SelectPrimitive.Viewport>{children}</SelectPrimitive.Viewport></SelectPrimitive.Content></SelectPrimitive.Portal>; }
export function SelectItem({ children, ...props }: React.ComponentProps<typeof SelectPrimitive.Item>) { return <SelectPrimitive.Item className="select-item" {...props}><SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText><SelectPrimitive.ItemIndicator><Check /></SelectPrimitive.ItemIndicator></SelectPrimitive.Item>; }
