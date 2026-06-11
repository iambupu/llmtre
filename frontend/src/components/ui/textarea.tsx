import * as React from "react"

import { cn } from "@/lib/utils"

/**
 * 功能：渲染项目统一样式的多行文本输入框。
 * 入参：props（React 组件属性）：透传到底层 DOM 或 Radix 原语。
 * 出参：ReactElement，渲染对应 UI 基础组件。
 * 异常：不显式抛异常；非法属性由 React 或底层组件处理。
 */
function Textarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return (
    <textarea
      data-slot="textarea"
      className={cn(
        "flex field-sizing-content min-h-16 w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-base transition-colors outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:bg-input/50 disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 md:text-sm dark:bg-input/30 dark:disabled:bg-input/80 dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40",
        className
      )}
      {...props}
    />
  )
}

export { Textarea }
