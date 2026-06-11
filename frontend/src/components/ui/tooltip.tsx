import * as React from "react"
import { Tooltip as TooltipPrimitive } from "radix-ui"

import { cn } from "@/lib/utils"

/**
 * 功能：提供 Tooltip 全局上下文与延迟策略封装。
 * 入参：props（React 组件属性）：透传到底层 DOM 或 Radix 原语。
 * 出参：ReactElement，渲染对应 UI 基础组件。
 * 异常：不显式抛异常；非法属性由 React 或底层组件处理。
 */
function TooltipProvider({
  delayDuration = 0,
  ...props
}: React.ComponentProps<typeof TooltipPrimitive.Provider>) {
  return (
    <TooltipPrimitive.Provider
      data-slot="tooltip-provider"
      delayDuration={delayDuration}
      {...props}
    />
  )
}

/**
 * 功能：封装单个 Tooltip 根节点状态。
 * 入参：props（React 组件属性）：透传到底层 Radix Tooltip Root。
 * 出参：ReactElement，渲染 Tooltip 根组件。
 * 异常：不显式抛异常；非法属性由 React 或 Radix 处理。
 */
function Tooltip({
  ...props
}: React.ComponentProps<typeof TooltipPrimitive.Root>) {
  return <TooltipPrimitive.Root data-slot="tooltip" {...props} />
}

/**
 * 功能：封装 Tooltip 触发元素，并向 Radix Trigger 转发 ref。
 * 入参：props（TooltipPrimitive.Trigger 属性）：透传到底层 DOM 或 Radix 原语。
 * 出参：ReactElement，渲染对应 UI 基础组件。
 * 异常：不显式抛异常；非法属性由 React 或底层组件处理。
 */
const TooltipTrigger = React.forwardRef<
  React.ComponentRef<typeof TooltipPrimitive.Trigger>,
  React.ComponentPropsWithoutRef<typeof TooltipPrimitive.Trigger>
>(({ ...props }, ref) => (
  <TooltipPrimitive.Trigger ref={ref} data-slot="tooltip-trigger" {...props} />
))
TooltipTrigger.displayName = "TooltipTrigger"

/**
 * 功能：渲染 Tooltip 浮层内容并合并项目样式。
 * 入参：props（React 组件属性）：透传到底层 DOM 或 Radix 原语。
 * 出参：ReactElement，渲染对应 UI 基础组件。
 * 异常：不显式抛异常；非法属性由 React 或底层组件处理。
 */
function TooltipContent({
  className,
  sideOffset = 0,
  children,
  ...props
}: React.ComponentProps<typeof TooltipPrimitive.Content>) {
  return (
    <TooltipPrimitive.Portal>
      <TooltipPrimitive.Content
        data-slot="tooltip-content"
        sideOffset={sideOffset}
        className={cn(
          "z-50 inline-flex w-fit max-w-xs origin-(--radix-tooltip-content-transform-origin) items-center gap-1.5 rounded-md bg-foreground px-3 py-1.5 text-xs text-background has-data-[slot=kbd]:pr-1.5 data-[side=bottom]:slide-in-from-top-2 data-[side=left]:slide-in-from-right-2 data-[side=right]:slide-in-from-left-2 data-[side=top]:slide-in-from-bottom-2 **:data-[slot=kbd]:relative **:data-[slot=kbd]:isolate **:data-[slot=kbd]:z-50 **:data-[slot=kbd]:rounded-sm data-[state=delayed-open]:animate-in data-[state=delayed-open]:fade-in-0 data-[state=delayed-open]:zoom-in-95 data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95",
          className
        )}
        {...props}
      >
        {children}
        <TooltipPrimitive.Arrow className="z-50 size-2.5 translate-y-[calc(-50%_-_2px)] rotate-45 rounded-[2px] bg-foreground fill-foreground" />
      </TooltipPrimitive.Content>
    </TooltipPrimitive.Portal>
  )
}

export { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger }
