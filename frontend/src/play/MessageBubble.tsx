import {
  BotIcon,
  BugIcon,
  SparklesIcon,
  UserRoundIcon,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { ActionGroupList } from "@/play/ActionGroupList";
import { groupActionsForDisplay } from "@/play/actionSupport";
import type { ChatMessage } from "@/play/persistenceSupport";
import type { SceneDisplayLabelResolver } from "@/play/sceneSupport";
import { rewriteActionLabelForDisplay } from "@/play/sceneSupport";

/**
 * 功能：渲染单条消息，并在最新 GM 消息下展示可提交的快捷动作按钮。
 * 入参：message（ChatMessage）：消息体；streaming（boolean，默认 false）：是否为流式临时消息；
 *   quickActions（string[]，默认 []）：后端原始动作；quickActionsDisabled（boolean，默认 false）：按钮禁用状态；
 *   sceneDisplayResolver（SceneDisplayLabelResolver，可选）：动作按钮显示名映射；
 *   onQuickAction（函数，可选）：点击快捷动作时提交原始动作值。
 * 出参：JSX.Element，包含角色徽标、时间、正文和可选快捷动作。
 * 异常：不抛异常；没有回调或动作时不显示快捷按钮。
 */
export function MessageBubble({
  message,
  streaming = false,
  quickActions = [],
  quickActionsDisabled = false,
  sceneDisplayResolver = new Map(),
  onQuickAction,
}: {
  message: ChatMessage;
  streaming?: boolean;
  quickActions?: string[];
  quickActionsDisabled?: boolean;
  sceneDisplayResolver?: SceneDisplayLabelResolver;
  onQuickAction?: (value: string) => Promise<void>;
}) {
  const quickActionGroups = groupActionsForDisplay(
    quickActions,
    null,
    sceneDisplayResolver
  );
  const displayText = rewriteActionLabelForDisplay(message.text, sceneDisplayResolver);
  const meta =
    message.role === "player"
      ? {
          label: "玩家",
          icon: UserRoundIcon,
          className: "bg-amber-500/15 text-amber-200 border-amber-400/40",
        }
      : message.role === "gm"
        ? {
            label: "旁白",
            icon: SparklesIcon,
            className: "bg-indigo-500/15 text-indigo-200 border-indigo-400/40",
          }
        : message.role === "error"
          ? {
              label: "错误",
              icon: BugIcon,
              className: "",
            }
          : {
              label: "系统",
              icon: BotIcon,
              className: "bg-emerald-500/15 text-emerald-200 border-emerald-400/40",
            };
  const RoleIcon = meta.icon;
  return (
    <article className={cn("message-bubble", `message-${message.role}`, streaming && "animate-pulse")}>
      <div className="flex items-center justify-between">
        <Badge variant={message.role === "error" ? "destructive" : "outline"} className={meta.className}>
          <RoleIcon data-icon="inline-start" />
          {meta.label}
        </Badge>
        <span className="text-xs text-muted-foreground">{message.at}</span>
      </div>
      <p>{displayText}</p>
      {quickActionGroups.length && onQuickAction ? (
        <div className="mt-3 flex flex-wrap gap-2">
          <ActionGroupList
            groups={quickActionGroups}
            isBusy={quickActionsDisabled}
            compact
            onSubmit={onQuickAction}
          />
        </div>
      ) : null}
    </article>
  );
}
