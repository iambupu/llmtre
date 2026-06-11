"use client"

import * as React from "react"
import { type VariantProps } from "class-variance-authority"
import { Toggle as TogglePrimitive } from "radix-ui"

import { cn } from "@/lib/utils"
import { toggleVariants } from "@/components/ui/toggle-variants"

/**
 * 功能：封装 Radix Toggle 并统一项目尺寸与样式变体。
 * 入参：props（React 组件属性）：透传到底层 DOM 或 Radix 原语。
 * 出参：ReactElement，渲染对应 UI 基础组件。
 * 异常：不显式抛异常；非法属性由 React 或底层组件处理。
 */
function Toggle({
  className,
  variant = "default",
  size = "default",
  ...props
}: React.ComponentProps<typeof TogglePrimitive.Root> &
  VariantProps<typeof toggleVariants>) {
  return (
    <TogglePrimitive.Root
      data-slot="toggle"
      className={cn(toggleVariants({ variant, size, className }))}
      {...props}
    />
  )
}

export { Toggle }
