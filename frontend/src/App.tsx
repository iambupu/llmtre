import { useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { createSession, getSession } from "@/api/sessions";
import { createTurn } from "@/api/turns";
import { commitSandbox, discardSandbox } from "@/api/sandbox";
import { getMemory, refreshMemory } from "@/api/memory";
import { resetSession } from "@/api/runtime";
import { useTurnStream } from "@/hooks/useTurnStream";
import { useDebugStore } from "@/stores/debugStore";
import { useStreamStore } from "@/stores/streamStore";
import { useUiStore } from "@/stores/uiStore";
import type { SessionPayload, TurnResult } from "@/types";

type ChatMessage = {
  role: "system" | "player" | "gm" | "error";
  text: string;
};

/**
 * 功能：A1 React 前端主页面，只消费后端返回并组织 UI，不做规则推断。
 * 入参：无。
 * 出参：JSX.Element。
 * 异常：组件内部不主动抛出异常，接口错误以消息展示与调试日志记录。
 */
export function App() {
  const [sessionId, setSessionId] = useState("");
  const [characterId, setCharacterId] = useState("player_01");
  const [userInput, setUserInput] = useState("");
  const [sessionData, setSessionData] = useState<SessionPayload | null>(null);
  const [turnData, setTurnData] = useState<TurnResult | null>(null);
  const [memoryText, setMemoryText] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([
    { role: "system", text: "A1 /app 已就绪：前端只展示后端返回结果。" },
  ]);

  const outputMode = useUiStore((s) => s.outputMode);
  const setOutputMode = useUiStore((s) => s.setOutputMode);
  const debugVisible = useUiStore((s) => s.debugVisible);
  const toggleDebug = useUiStore((s) => s.toggleDebug);
  const isBusy = useStreamStore((s) => s.isBusy);
  const streamingText = useStreamStore((s) => s.streamingText);
  const addLog = useDebugStore((s) => s.addLog);
  const traceId = useDebugStore((s) => s.traceId);
  const lastRequest = useDebugStore((s) => s.lastRequest);
  const lastSseEvent = useDebugStore((s) => s.lastSseEvent);
  const logs = useDebugStore((s) => s.logs);

  const stream = useTurnStream();

  const quickActions = useMemo(() => {
    if (turnData?.quick_actions?.length) {
      return turnData.quick_actions;
    }
    const affordances = turnData?.affordances ?? turnData?.scene_snapshot?.affordances ?? [];
    return affordances
      .filter((item) => item.enabled)
      .map((item) => item.user_input || item.label)
      .filter(Boolean) as string[];
  }, [turnData]);

  const createSessionMutation = useMutation({
    mutationFn: async () => {
      const payload = await createSession({
        character_id: characterId || undefined,
        sandbox_mode: false,
      });
      return payload;
    },
    onSuccess: (payload) => {
      setSessionId(payload.session_id);
      setSessionData(payload);
      addLog(`已创建会话: ${payload.session_id}`);
      setMessages((prev) => [...prev, { role: "system", text: `会话创建成功：${payload.session_id}` }]);
    },
    onError: (err) => {
      setMessages((prev) => [...prev, { role: "error", text: String(err) }]);
    },
  });

  const loadSessionMutation = useMutation({
    mutationFn: async () => getSession(sessionId),
    onSuccess: (payload) => {
      setSessionData(payload);
      addLog(`已加载会话: ${sessionId}`);
      setMessages((prev) => [...prev, { role: "system", text: `会话已加载：${sessionId}` }]);
    },
    onError: (err) => {
      setMessages((prev) => [...prev, { role: "error", text: String(err) }]);
    },
  });

  const memoryMutation = useMutation({
    mutationFn: async () => getMemory(sessionId),
    onSuccess: (payload) => setMemoryText(payload.summary ?? payload.text ?? ""),
  });

  const refreshMemoryMutation = useMutation({
    mutationFn: async () => refreshMemory(sessionId),
    onSuccess: (payload) => setMemoryText(payload.summary ?? payload.text ?? ""),
  });

  const resetMutation = useMutation({
    mutationFn: async () => resetSession(sessionId, true),
    onSuccess: (payload) => {
      setSessionData(payload);
      setTurnData(null);
      addLog("会话已重置");
    },
  });

  const commitMutation = useMutation({
    mutationFn: async () => commitSandbox(sessionId),
    onSuccess: () => addLog("沙盒并入成功"),
  });

  const discardMutation = useMutation({
    mutationFn: async () => discardSandbox(sessionId),
    onSuccess: () => addLog("沙盒回滚成功"),
  });

  async function submitTurn(text: string) {
    if (!sessionId) {
      setMessages((prev) => [...prev, { role: "error", text: "请先创建或加载会话。" }]);
      return;
    }
    const finalText = text.trim();
    if (!finalText) {
      return;
    }
    setUserInput("");
    setMessages((prev) => [...prev, { role: "player", text: finalText }]);
    addLog(`提交回合: ${outputMode}`);
    try {
      let result: TurnResult;
      if (outputMode === "stream") {
        result = await stream.run(sessionId, {
          user_input: finalText,
          character_id: characterId || undefined,
          sandbox_mode: false,
        });
      } else {
        result = await createTurn(sessionId, {
          user_input: finalText,
          character_id: characterId || undefined,
          sandbox_mode: false,
        });
      }
      setTurnData(result);
      setMessages((prev) => [...prev, { role: "gm", text: result.final_response }]);
      if (result.memory_summary) {
        setMemoryText(result.memory_summary);
      }
      setSessionData((prev) =>
        prev
          ? {
              ...prev,
              current_session_turn_id: result.session_turn_id,
              scene_snapshot: result.scene_snapshot ?? prev.scene_snapshot,
              active_character: result.active_character ?? prev.active_character,
              memory_summary: result.memory_summary ?? prev.memory_summary,
            }
          : prev
      );
      addLog(`回合完成: s_turn=${result.session_turn_id}, r_turn=${result.runtime_turn_id}`);
    } catch (err) {
      setMessages((prev) => [...prev, { role: "error", text: String(err) }]);
      addLog(`回合失败: ${String(err)}`);
    }
  }

  return (
    <div className="layout">
      <header className="topbar">
        <h1>LLM TRE /app (A1)</h1>
        <div className="row">
          <label>
            角色
            <input value={characterId} onChange={(e) => setCharacterId(e.target.value)} />
          </label>
          <label>
            会话
            <input value={sessionId} onChange={(e) => setSessionId(e.target.value)} placeholder="sess_xxx" />
          </label>
          <button onClick={() => createSessionMutation.mutate()} disabled={isBusy}>新会话</button>
          <button onClick={() => loadSessionMutation.mutate()} disabled={!sessionId || isBusy}>加载</button>
          <button onClick={() => resetMutation.mutate()} disabled={!sessionId || isBusy}>重置</button>
          <button onClick={toggleDebug}>调试: {debugVisible ? "开" : "关"}</button>
        </div>
      </header>

      <main className="main">
        <section className="card">
          <h2>会话与场景</h2>
          <pre>{JSON.stringify(sessionData?.scene_snapshot ?? turnData?.scene_snapshot ?? {}, null, 2)}</pre>
        </section>

        <section className="card">
          <h2>回合记录</h2>
          <div className="messages">
            {messages.map((m, idx) => (
              <div key={`${m.role}-${idx}`} className={`msg msg-${m.role}`}>
                <strong>{m.role}</strong>
                <p>{m.text}</p>
              </div>
            ))}
            {isBusy && streamingText ? (
              <div className="msg msg-gm">
                <strong>gm(streaming)</strong>
                <p>{streamingText}</p>
              </div>
            ) : null}
          </div>
          <div className="actions">
            {quickActions.map((act, idx) => (
              <button key={`${act}-${idx}`} onClick={() => void submitTurn(act)} disabled={isBusy}>
                {act}
              </button>
            ))}
          </div>
          <div className="row">
            <select value={outputMode} onChange={(e) => setOutputMode(e.target.value as "sync" | "stream")}>
              <option value="stream">stream</option>
              <option value="sync">sync</option>
            </select>
            <input
              value={userInput}
              onChange={(e) => setUserInput(e.target.value)}
              placeholder="输入行动"
              disabled={isBusy}
            />
            <button onClick={() => void submitTurn(userInput)} disabled={isBusy}>发送</button>
            <button onClick={stream.abort} disabled={!isBusy}>停止接收</button>
          </div>
        </section>

        <section className="card">
          <h2>状态与调试入口</h2>
          <div className="row">
            <button onClick={() => memoryMutation.mutate()} disabled={!sessionId}>读取记忆</button>
            <button onClick={() => refreshMemoryMutation.mutate()} disabled={!sessionId}>刷新记忆</button>
            <button onClick={() => commitMutation.mutate()} disabled={!sessionId}>并入沙盒</button>
            <button onClick={() => discardMutation.mutate()} disabled={!sessionId}>回滚沙盒</button>
          </div>
          <p><strong>trace_id:</strong> {traceId || "-"}</p>
          <pre>{memoryText || sessionData?.memory_summary || ""}</pre>
        </section>
      </main>

      {debugVisible ? (
        <aside className="debug">
          <h2>调试面板</h2>
          <h3>lastRequest</h3>
          <pre>{JSON.stringify(lastRequest, null, 2)}</pre>
          <h3>lastSseEvent</h3>
          <pre>{JSON.stringify(lastSseEvent, null, 2)}</pre>
          <h3>turn debug_trace</h3>
          <pre>{JSON.stringify(turnData?.debug_trace ?? turnData?.trace ?? {}, null, 2)}</pre>
          <h3>status log</h3>
          <pre>{JSON.stringify(logs, null, 2)}</pre>
        </aside>
      ) : null}
    </div>
  );
}
