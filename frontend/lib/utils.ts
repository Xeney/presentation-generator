import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/** Склейка классов в стиле shadcn/ui: конфликтующие tailwind-классы решаются в пользу последнего. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
