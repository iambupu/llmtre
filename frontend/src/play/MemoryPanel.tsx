import {
  BookOpenIcon,
  EyeOffIcon,
  RotateCcwIcon,
  ShieldIcon,
  SwordsIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { SceneDisplayLabelResolver } from "@/play/sceneSupport";
import { rewriteActionLabelForDisplay } from "@/play/sceneSupport";

/**
 * 功能：渲染会话记忆摘要，并把摘要里的系统 ID 转为玩家可读显示文本。
 * 入参：memoryText（string）：后端记忆摘要；sceneDisplayResolver（SceneDisplayLabelResolver）：显示名映射；
 *   disabled（boolean）：会话按钮禁用状态；onRead/onRefresh/onCommit/onDiscard：记忆操作回调。
 * 出参：JSX.Element，包含摘要文本和读取、刷新、清空、并入、回滚按钮。
 * 异常：不抛异常；没有摘要时展示空态文案，按钮禁用逻辑由入参控制。
 */
export function MemoryPanel({
  memoryText,
  sceneDisplayResolver,
  disabled,
  onRead,
  onRefresh,
  onCommit,
  onDiscard,
}: {
  memoryText: string;
  sceneDisplayResolver: SceneDisplayLabelResolver;
  disabled: boolean;
  onRead: () => void;
  onRefresh: () => void;
  onCommit: () => void;
  onDiscard: () => void;
}) {
  return (
    <Card className="border-primary/20">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <BookOpenIcon data-icon="inline-start" />
          记忆摘要
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <p className="max-h-28 overflow-auto rounded-lg border bg-muted/30 p-3 text-sm text-muted-foreground">
          {memoryText
            ? rewriteActionLabelForDisplay(memoryText, sceneDisplayResolver)
            : "暂无记忆摘要。"}
        </p>
        <div className="grid grid-cols-3 gap-2">
          <Button variant="secondary" disabled={disabled} onClick={onRead}>
            <BookOpenIcon data-icon="inline-start" />
            读取
          </Button>
          <Button variant="secondary" disabled={disabled} onClick={onRefresh}>
            <RotateCcwIcon data-icon="inline-start" />
            刷新
          </Button>
          <Button variant="outline" disabled={!memoryText}>
            <EyeOffIcon data-icon="inline-start" />
            清空
          </Button>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <Button variant="outline" disabled={disabled} onClick={onCommit}>
            <ShieldIcon data-icon="inline-start" />
            并入
          </Button>
          <Button variant="outline" disabled={disabled} onClick={onDiscard}>
            <SwordsIcon data-icon="inline-start" />
            回滚
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
