/**
 * 功能：渲染调试面板顶部统计卡片。
 * 入参：label（string）：指标名；value（string）：已格式化的指标值。
 * 出参：JSX.Element。
 * 异常：不抛异常；调用方负责传入可展示文本。
 */
export function DebugStatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-primary/20 bg-muted/20 p-3 text-center">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-2xl font-semibold">{value}</p>
    </div>
  );
}
