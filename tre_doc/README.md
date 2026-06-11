# `tre_doc/` — TRE 技术文档合集

## 用途

`tre_doc/` 是 TRE 项目的**集中式技术文档目录**，提供完整的模块架构分析与子模块详解，便于新开发者快速理解项目全貌。

---

## 文档清单

### 架构总览

| 文件 | 内容 |
|------|------|
| `TRE_模块架构.md` | 全局架构总览、四层解耦模型、模块间依赖关系与数据流 |

### 子模块详解（按 layer 自底向上排列）

| Layer | 文件 | 覆盖内容 |
|-------|------|---------|
| 持久层 | `TRE_子模块_state.md` | Pydantic 模型体系、Active/Shadow 双表快照、Repository 模式 |
| 核心引擎 | `TRE_子模块_core.md` | EventBus（HookContext、冲突检测）、runtime_logging |
| 资源层（工具） | `TRE_子模块_tools.md` | RAG、掷骰、沙盒、实体管理、任务系统、MOD 管理、Pack 验证 |
| 逻辑层 | `TRE_子模块_game_workflows.md` | LangGraph 内环 + LlamaIndex 外环双轨工作流 |
| 智能层 | `TRE_子模块_agents.md` | NLUAgent（rule_first）、GMAgent（llm_first）、ClarifierAgent、EvolutionAgent |
| Web 层 | `TRE_子模块_web_api.md` | Flask app factory、8 Blueprint、SessionService、WebSessionStore |
| 前端 | `TRE_子模块_frontend.md` | React + Vite + TypeScript、API 层收口、状态边界 |

### 扩展模块

| 文件 | 覆盖内容 |
|------|---------|
| `TRE_子模块_config.md` | 6 个配置文件的 schema 与加载机制 |
| `TRE_子模块_mods.md` | MOD 双重扩展机制（静态合并 + 动态钩子） |
| `TRE_子模块_story_packs.md` | 剧本包 Manifest/场景/传说契约规范与验证 |

---

## 阅读指南

```
首次阅读顺序（推荐）：
  1. TRE_模块架构.md                  ← 建立全局认知
  2. TRE_子模块_state.md             ← 数据模型是一切的基础
  3. TRE_子模块_core.md              ← 核心引擎
  4. TRE_子模块_game_workflows.md    ← 主循环编排
  5. TRE_子模块_agents.md            ← 智能体逻辑
  6. TRE_子模块_tools.md             ← 确定性工具集
  7. TRE_子模块_web_api.md           ← Web 接口层
  8. TRE_子模块_frontend.md          ← 用户界面
  9. config / mods / story_packs      ← 按需查阅
```

---

## 更新说明

- 这些文档是静态参考，与代码库保持同步
- 修改对应文件是请同步文档，避免造成文档落后
