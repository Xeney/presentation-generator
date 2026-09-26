"use client";

import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";

import { cn } from "@/lib/utils";

const buttonVariants = cva("btn", {
  variants: {
    variant: {
      default: "btn-primary",
      secondary: "",
      outline: "",
      ghost: "btn-ghost",
      destructive: "btn-danger",
      link: "btn-ghost underline underline-offset-4",
    },
    size: {
      default: "",
      sm: "btn-sm",
      lg: "btn-lg",
      icon: "btn-icon",
    },
  },
  defaultVariants: { variant: "default", size: "default" },
});

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, ...props }, ref) => (
    <button ref={ref} className={cn(buttonVariants({ variant, size, className }))} {...props} />
  ),
);
Button.displayName = "Button";

export { Button, buttonVariants };
