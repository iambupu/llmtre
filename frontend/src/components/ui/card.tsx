import * as React from "react"

import { cn } from "@/lib/utils"

/**
 * 功能：渲染卡片根容器并支持默认与紧凑尺寸。
 * 入参：props（React 组件属性）：透传到底层 DOM 或 Radix 原语。
 * 出参：ReactElement，渲染对应 UI 基础组件。
 * 异常：不显式抛异常；非法属性由 React 或底层组件处理。
 */
function Card({
  className,
  size = "default",
  ...props
}: React.ComponentProps<"div"> & { size?: "default" | "sm" }) {
  return (
    <div
      data-slot="card"
      data-size={size}
      className={cn(
        "group/card flex flex-col gap-4 overflow-hidden rounded-xl bg-card py-4 text-sm text-card-foreground ring-1 ring-foreground/10 has-data-[slot=card-footer]:pb-0 has-[>img:first-child]:pt-0 data-[size=sm]:gap-3 data-[size=sm]:py-3 data-[size=sm]:has-data-[slot=card-footer]:pb-0 *:[img:first-child]:rounded-t-xl *:[img:last-child]:rounded-b-xl",
        className
      )}
      {...props}
    />
  )
}

/**
 * 功能：渲染卡片头部布局区域。
 * 入参：props（React 组件属性）：透传到底层 DOM 或 Radix 原语。
 * 出参：ReactElement，渲染对应 UI 基础组件。
 * 异常：不显式抛异常；非法属性由 React 或底层组件处理。
 */
function CardHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-header"
      className={cn(
        "group/card-header @container/card-header grid auto-rows-min items-start gap-1 rounded-t-xl px-4 group-data-[size=sm]/card:px-3 has-data-[slot=card-action]:grid-cols-[1fr_auto] has-data-[slot=card-description]:grid-rows-[auto_auto] [.border-b]:pb-4 group-data-[size=sm]/card:[.border-b]:pb-3",
        className
      )}
      {...props}
    />
  )
}

/**
 * 功能：渲染卡片标题文本区域。
 * 入参：props（React 组件属性）：透传到底层 DOM 或 Radix 原语。
 * 出参：ReactElement，渲染对应 UI 基础组件。
 * 异常：不显式抛异常；非法属性由 React 或底层组件处理。
 */
function CardTitle({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-title"
      className={cn(
        "text-base leading-snug font-medium group-data-[size=sm]/card:text-sm",
        className
      )}
      {...props}
    />
  )
}

/**
 * 功能：渲染卡片描述文本区域。
 * 入参：props（React 组件属性）：透传到底层 DOM 或 Radix 原语。
 * 出参：ReactElement，渲染对应 UI 基础组件。
 * 异常：不显式抛异常；非法属性由 React 或底层组件处理。
 */
function CardDescription({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-description"
      className={cn("text-sm text-muted-foreground", className)}
      {...props}
    />
  )
}

/**
 * 功能：渲染卡片头部右侧操作区域。
 * 入参：props（React 组件属性）：透传到底层 DOM 或 Radix 原语。
 * 出参：ReactElement，渲染对应 UI 基础组件。
 * 异常：不显式抛异常；非法属性由 React 或底层组件处理。
 */
function CardAction({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-action"
      className={cn(
        "col-start-2 row-span-2 row-start-1 self-start justify-self-end",
        className
      )}
      {...props}
    />
  )
}

/**
 * 功能：渲染卡片主体内容区域。
 * 入参：props（React 组件属性）：透传到底层 DOM 或 Radix 原语。
 * 出参：ReactElement，渲染对应 UI 基础组件。
 * 异常：不显式抛异常；非法属性由 React 或底层组件处理。
 */
function CardContent({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-content"
      className={cn("px-4 group-data-[size=sm]/card:px-3", className)}
      {...props}
    />
  )
}

/**
 * 功能：渲染卡片底部操作或摘要区域。
 * 入参：props（React 组件属性）：透传到底层 DOM 或 Radix 原语。
 * 出参：ReactElement，渲染对应 UI 基础组件。
 * 异常：不显式抛异常；非法属性由 React 或底层组件处理。
 */
function CardFooter({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-footer"
      className={cn(
        "flex items-center rounded-b-xl border-t bg-muted/50 p-4 group-data-[size=sm]/card:p-3",
        className
      )}
      {...props}
    />
  )
}

export {
  Card,
  CardHeader,
  CardFooter,
  CardTitle,
  CardAction,
  CardDescription,
  CardContent,
}
