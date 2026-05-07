import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * 功能：合并 Tailwind className，供 shadcn/ui 组件处理变体与条件样式。
 * 入参：inputs（ClassValue[]）：任意 clsx 支持的 className 输入。
 * 出参：string，已去除冲突 Tailwind 类后的 className。
 * 异常：不主动抛出异常；非法输入按 clsx/twMerge 默认策略忽略或转换。
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
