# TRE 子模块详解：`frontend`

## 模块定位

**层级**：资源层 — 用户界面  
**职责**：TRE 的 React + Vite + TypeScript 正式前端工程，通过 `/app` 路由被 Flask 同源托管。负责游戏交互界面、调试面板、Agent 可视化和配置管理。

---

## 目录结构

```
frontend/
├── package.json             # 依赖 + Vite/React 配置
├── tsconfig.json            # TypeScript 配置
├── vite.config.ts           # Vite 构建配置（带 /api proxy）
├── index.html               # HTML 入口
├── src/
│   ├── main.tsx             # React 入口
│   ├── App.tsx              # 根组件 + 路由
│   ├── api/                 # [核心] API 通信层
│   │   ├── client.ts        # 统一 HTTP 客户端（request_id, trace_id）
│   │   └── sessions.ts      # 会话/回合 API 调用
│   ├── components/          # UI 组件
│   │   ├── ChatPanel/       # 聊天面板
│   │   ├── DebugPanel/      # 调试面板（request_id, trace_id, SSE 明细）
│   │   ├── SessionList/     # 会话列表
│   │   └── common/          # 通用组件
│   ├── hooks/               # 自定义 Hooks
│   │   └── useChat.ts       # 聊天交互 Hook
│   ├── stores/              # 状态管理
│   │   └── debugStore.ts    # Zustand — 调试面板状态
│   ├── lib/                 # 工具库
│   │   └── sse.ts           # SSE 流式解析
│   └── test/                # 测试
└── dist/                    # 构建产物（Flask 同源托管）
```

---

## 技术栈

| 层 | 技术 | 用途 |
|----|------|------|
| 框架 | React 18+ | UI 组件化 |
| 构建 | Vite | 开发服务器 + 生产构建 |
| 语言 | TypeScript | 类型安全 |
| 状态管理 | Zustand | 调试面板/UI 状态 |
| 后端数据 | TanStack Query | 后端数据缓存与同步 |
| 样式 | CSS Modules / Tailwind | 界面样式 |
| 流式 | fetch SSE | 流式响应消费 |

---

## 架构分层

### 1. API 层（`src/api/`）

**设计原则**：所有 HTTP 请求收口到 API 层，统一处理 `request_id`、`trace_id`、JSON 解析与错误处理。

```typescript
// src/api/client.ts（示意）
class ApiClient {
  private baseUrl: string;
  private requestId: string;

  async request<T>(path: string, options: RequestInit): Promise<T> {
    // 自动注入 request_id、trace_id
    // 统一 JSON 解析
    // 错误处理与调试记录
  }

  async createSession(packId: string): Promise<SessionResponse>;
  async sendMessage(sessionId: string, text: string): Promise<TurnResponse>;
  async streamMessage(sessionId: string, text: string): Promise<ReadableStream>;
}
```

**流式接口**：
- `POST /api/sessions/{session_id}/turns/stream` — SSE 风格接口
- 事件类型：`received`、progress、`gm_delta`、detail、`done`、`error`
- 前端必须消费到 `done` 或 `error` 后再下结论

### 2. 状态管理（`src/stores/`）

```typescript
// Zustand store — 只用于 UI 状态
interface DebugStore {
  lastRequest: ApiRequest | null;
  lastSseEvent: SseEvent | null;
  debugTrace: string[];
  statusLog: LogEntry[];
  // ... 调试面板相关
}
```

**状态边界规则**：
| 状态类型 | 存放位置 |
|---------|---------|
| 后端数据（会话、回合、角色） | TanStack Query |
| UI 状态（面板展开/收起） | Zustand |
| 流式临时态（当前 SSE 事件） | Zustand |
| 组件内部输入 | React local state |

### 3. 组件树（`src/components/`）

```
App
├── SessionList        — 会话列表面板
├── ChatPanel          — 主聊天区
│   ├── MessageList    — 消息列表
│   ├── MessageInput   — 输入框
│   └── StreamStatus   — 流式状态指示器
├── DebugPanel         — 调试面板
│   ├── RequestDetail  — 请求详情
│   ├── SseEventLog    — SSE 事件明细
│   └── TraceView      — 调用链追踪
└── AgentVisualizer    — Agent 运行状态可视化（只读）
```

---

## 开发与构建

```bash
# 开发（Vite dev server + proxy → Flask）
cd frontend
npm run dev

# 生产构建
npm run build
# 产物输出到 frontend/dist/
# Flask 通过 web_api/blueprints/playground.py 同源托管
```

Vite 开发服务器通过 proxy 将 `/api` 转发到 Flask（`http://localhost:5000`）。

---

## 约束与约定

1. **前端不做规则计算** — HP/MP、背包、位置、任务以 SQLite 契约为准
2. **前端不做状态写入** — 不直接写配置、不回写状态
3. **调试面板是验收资产** — `request_id`、`trace_id`、`lastRequest`、`lastSseEvent`、`debug_trace` 必须保留
4. **Agent 可视化只读** — 展示 NLU/GM/Memory/Trace 状态，不做工作流编辑器
5. **组件不直接散落 fetch** — 全部走 `src/api/client.ts`

---

## 依赖关系

```
frontend
  └── web_api/       # 通过 HTTP API 调用后端（/api/*）
  └── (无反向依赖)    — 前端是纯消费者
```
