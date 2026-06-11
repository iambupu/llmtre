import { WandSparklesIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { ActionCategoryGroup } from "@/play/actionSupport";

/**
 * 功能：按动作类别展示可提交按钮，只改变玩家阅读结构，不修改提交文本。
 * 入参：groups（ActionCategoryGroup[]）：已分类动作；title（string | undefined）：可选标题；
 *   isBusy（boolean）：回合请求状态；compact（boolean，默认 false）：是否使用紧凑布局；
 *   onSubmit（函数）：提交动作文本。
 * 出参：JSX.Element。
 * 异常：不抛异常；空分组由调用方过滤。
 */
export function ActionGroupList({
  groups,
  title,
  isBusy,
  compact = false,
  onSubmit,
}: {
  groups: ActionCategoryGroup[];
  title?: string;
  isBusy: boolean;
  compact?: boolean;
  onSubmit: (value: string) => Promise<void>;
}) {
  return (
    <div className={cn("space-y-3", compact && "space-y-2")}>
      {title ? <p className="text-xs text-muted-foreground">{title}</p> : null}
      {groups.map((group) => {
        const Icon = group.icon;
        return (
          <div key={group.key} className="space-y-2">
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Icon data-icon="inline-start" />
              {group.label}
            </div>
            <div className="flex flex-wrap gap-2">
              {group.actions.map((action) => (
                <Button
                  key={`${group.key}-${action.value}`}
                  variant="outline"
                  size="sm"
                  disabled={isBusy}
                  onClick={() => void onSubmit(action.value)}
                >
                  <WandSparklesIcon data-icon="inline-start" />
                  {action.label}
                </Button>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}
