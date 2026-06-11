import {
  BookOpenIcon,
  LoaderCircleIcon,
  PackageIcon,
  Trash2Icon,
  UploadIcon,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Textarea } from "@/components/ui/textarea";
import type { StoryPackDetailPayload, StoryPackSummary } from "@/types";
import { textValue } from "@/play/displayTextSupport";

/**
 * 功能：解析剧本起点的玩家可读显示名，避免入口面板泄漏内部 scene_id。
 * 入参：summary（StoryPackSummary | null | undefined）：后端剧本摘要，旧缓存可能缺少 start_scene_title。
 * 出参：string，优先返回后端派生的场景展示名，缺失时降级到 start_scene_id。
 * 异常：不抛异常；字段缺失或非字符串时按空文本降级。
 */
function storyPackStartSceneLabel(summary?: StoryPackSummary | null): string {
  return textValue(summary?.start_scene_title, textValue(summary?.start_scene_id, "未记录起点"));
}

/**
 * 功能：渲染 A2-Release 剧本管理面板，提供玩家向只读预览、JSON 导入和非官方包删除。
 * 入参：storyPacks/diagnostics/selectedPack/preview 为后端 Story Pack 状态；
 *   importDraft 为上传草稿；各 on* 回调只触发后端请求。
 * 出参：JSX.Element。
 * 异常：不抛异常；缺失预览或诊断时降级为空列表展示，技术字段交由调试面板承载。
 */
export function PackReleasePanel({
  storyPacks,
  diagnostics,
  selectedPack,
  preview,
  importDraft,
  disabled,
  isLoading,
  isPreviewLoading,
  isImporting,
  isDeleting,
  onPreview,
  onImportDraftChange,
  onImport,
  onDelete,
}: {
  storyPacks: StoryPackSummary[];
  diagnostics: Record<string, string[]>;
  selectedPack: StoryPackSummary | null;
  preview: StoryPackDetailPayload | null;
  importDraft: string;
  disabled: boolean;
  isLoading: boolean;
  isPreviewLoading: boolean;
  isImporting: boolean;
  isDeleting: boolean;
  onPreview: () => void;
  onImportDraftChange: (value: string) => void;
  onImport: () => void;
  onDelete: () => void;
}) {
  const diagnosticKeys = Object.keys(diagnostics);
  const previewScenes = Array.isArray(preview?.scenes) ? preview.scenes : [];
  const canDelete = Boolean(selectedPack);
  const firstPreviewScene = previewScenes[0];
  const selectedStartSceneLabel = storyPackStartSceneLabel(selectedPack);
  const previewStartSceneLabel = storyPackStartSceneLabel(preview?.summary);
  return (
    <Card className="border-primary/20">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <PackageIcon data-icon="inline-start" />
          剧本入口
        </CardTitle>
        <CardAction>
          <Badge variant="outline">{isLoading ? "--" : storyPacks.length}</Badge>
        </CardAction>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div className="rounded-lg border border-primary/25 bg-muted/30 p-3">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="truncate text-sm font-medium">
                {selectedPack ? selectedPack.title : "选择或导入外部剧本"}
              </div>
              <div className="mt-1 text-xs text-muted-foreground">
                {selectedPack
                  ? `起点 ${selectedStartSceneLabel} · ${selectedPack.version}`
                  : "游戏内容必须来自合法剧本包"}
              </div>
            </div>
            {selectedPack ? (
              <Badge variant="secondary">
                {selectedPack.scene_count} 场景
              </Badge>
            ) : null}
          </div>
          <div className="mt-3 grid grid-cols-3 gap-2 text-center text-xs">
            <div className="rounded-md border bg-background/35 p-2">
              <div className="font-semibold">{selectedPack?.interaction_count ?? "--"}</div>
              <div className="text-muted-foreground">交互</div>
            </div>
            <div className="rounded-md border bg-background/35 p-2">
              <div className="font-semibold">{selectedPack?.quest_count ?? "--"}</div>
              <div className="text-muted-foreground">任务</div>
            </div>
            <div className="rounded-md border bg-background/35 p-2">
              <div className="font-semibold">{selectedPack?.trigger_count ?? "--"}</div>
              <div className="text-muted-foreground">触发</div>
            </div>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-2">
            <Button
              variant="secondary"
              disabled={disabled || !selectedPack || isPreviewLoading}
              onClick={onPreview}
            >
              {isPreviewLoading ? (
                <LoaderCircleIcon data-icon="inline-start" className="animate-spin" />
              ) : (
                <BookOpenIcon data-icon="inline-start" />
              )}
              预览
            </Button>
            <Button
              variant="outline"
              disabled={disabled || !canDelete || isDeleting}
              onClick={onDelete}
            >
              {isDeleting ? (
                <LoaderCircleIcon data-icon="inline-start" className="animate-spin" />
              ) : (
                <Trash2Icon data-icon="inline-start" />
              )}
              删除
            </Button>
          </div>
        </div>

        {preview ? (
          <ScrollArea className="h-36 rounded-lg border bg-muted/25">
            <div className="space-y-2 p-3">
              <div className="text-sm font-medium">
                {preview.summary.title}
              </div>
              <div className="text-xs text-muted-foreground">
                起始场景：{previewStartSceneLabel}
              </div>
              {firstPreviewScene ? (
                <p className="rounded-md border bg-background/30 p-2 text-sm leading-6">
                  {textValue(firstPreviewScene.summary, "该剧本暂未提供开场摘要。")}
                </p>
              ) : null}
              {previewScenes.slice(0, 3).map((scene, index) => (
                <div key={`${textValue(scene.scene_id, "scene")}-${index}`} className="text-sm">
                  <span className="font-medium">
                    {textValue(scene.display_name ?? scene.scene_id, "场景")}
                  </span>
                  <span className="ml-2 text-muted-foreground">
                    {textValue(scene.summary, "无摘要")}
                  </span>
                </div>
              ))}
            </div>
          </ScrollArea>
        ) : null}

        {diagnosticKeys.length ? (
          <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-xs">
            {diagnosticKeys.slice(0, 3).map((key) => (
              <div key={key}>
                <span className="font-medium">{key}</span>：{diagnostics[key].join("；")}
              </div>
            ))}
          </div>
        ) : null}

        <details className="rounded-lg border border-primary/20 bg-background/35 p-3" open={!storyPacks.length}>
          <summary className="cursor-pointer text-sm font-medium">
            导入或编辑剧本包 JSON
          </summary>
          <div className="mt-3 flex flex-col gap-3">
            <Textarea
              className="h-44 resize-none border-primary/20 bg-background/40 font-mono text-xs"
              value={importDraft}
              placeholder="粘贴外部剧本包 JSON。界面不会预置固定剧情内容。"
              disabled={disabled || isImporting}
              onChange={(event) => onImportDraftChange(event.target.value)}
            />
            <Button disabled={disabled || isImporting} onClick={onImport}>
              {isImporting ? (
                <LoaderCircleIcon data-icon="inline-start" className="animate-spin" />
              ) : (
                <UploadIcon data-icon="inline-start" />
              )}
              导入剧本
            </Button>
          </div>
        </details>
      </CardContent>
    </Card>
  );
}
