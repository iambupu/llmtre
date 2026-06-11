# TRE 子模块详解：`config`

## 模块定位

**层级**：全局配置层  
**职责**：承载 TRE 所有可配置项，包括 Agent 模型绑定、RAG 参数、MOD 注册表、主循环规则等，支持运行时热加载。

---

## 目录结构

```
config/
├── agent_model_config.yml     # Agent ↔ LLM 模型绑定与模式控制
├── agent_model_loader.py      # YAML 配置加载器
├── main_loop_rules.json       # 主循环行为规则（热加载）
├── mod_registry.yml           # MOD 注册表（运行时更新）
├── rag_config.yml             # RAG 检索配置
├── rag_import_rules.json      # RAG 导入规则
└── api/                       # API 相关配置（可选）
    └── ...
```

---

## 各配置文件详解

### 1. `agent_model_config.yml` — Agent 模型绑定

控制各 Agent 是否使用真实 LLM、模式选择、profile 绑定。

```yaml
bindings:
  agents.nlu:
    enabled: true
    mode: "rule_first"        # "deterministic" | "rule_first" | "llm_only"
    llm_profile: "default"    # 引用 profiles.llm.default

  agents.gm:
    enabled: true
    mode: "llm_first"         # "deterministic" | "llm_first" | "template_only"
    llm_profile: "default"

  agents.evolution:
    enabled: false            # 默认关闭
    mode: "deterministic"
    llm_profile: null

profiles:
  llm:
    default:
      provider: "openai"
      model: "gpt-4o"
      temperature: 0.7
      max_tokens: 2048
  embedding:
    default:
      provider: "openai"
      model: "text-embedding-3-small"
```

**纯确定性模式**（验收时使用）：
```yaml
bindings:
  agents.nlu:
    enabled: false
    mode: "deterministic"
    llm_profile: null
  agents.gm:
    enabled: false
    mode: "deterministic"
    llm_profile: null
```

### 2. `main_loop_rules.json` — 主循环规则

```json
{
  "nlu": {
    "enabled": true,
    "strictness": 0.8
  },
  "rag": {
    "read_only_enabled": true,
    "auto_initialize": false
  },
  "outer_loop": {
    "enabled": false
  },
  "log_level": "INFO"
}
```

可运行时热加载，覆盖 `main_loop_config.py` 中的 `DEFAULT_MAIN_LOOP_RULES`。

### 3. `rag_config.yml` — RAG 检索配置

```yaml
embedding:
  model: "text-embedding-3-small"
  dimension: 1536
  chunk_size: 512
  chunk_overlap: 64

retrieval:
  top_k: 5
  min_score: 0.7
  hybrid_search: true    # 向量 + 关键字混合检索

knowledge_graph:
  extraction_enabled: true  # 默认启用属性图谱提取
```

### 4. `mod_registry.yml` — MOD 注册表

```yaml
mods:
  holy_mod:
    path: "mods/holy_mod"
    enabled: true
    priority: 10
    hooks: ["on_turn_start", "on_nlu_parse", "on_gm_render"]
    conflicts:
      - with: "vampire_mod"
        strategy: "reject"    # 拒绝共存
  vampire_mod:
    path: "mods/vampire_mod"
    enabled: true
    priority: 5
    hooks: ["on_gm_render", "on_scene_build"]
```

优先级 `smart_merge` 在 `core.EventBus` 中执行。

### 5. `rag_import_rules.json` — 文档导入规则

```json
{
  "allowed_extensions": [".md", ".pdf", ".txt"],
  "max_file_size_mb": 10,
  "default_group": "lore",
  "sync_on_import": false
}
```

---

## 配置加载机制

```python
# config/agent_model_loader.py（示意）
class AgentModelLoader:
    def load(self) -> AgentModelConfig:
        # 读取 agent_model_config.yml
        # 解析 profiles.bindings
        # 返回 Pydantic 模型

    def reload(self) -> AgentModelConfig:
        # 运行时重载（不重启进程）
```

配置层面的"法典"原则：
- YAML 用于人类可读的静态配置
- JSON 用于运行时热加载
- `pyproject.toml` 在项目根目录补充 Python 工具链配置

---

## 依赖关系

```
config
  ├── (自包含) — 被所有模块依赖（agents, game_workflows, tools, web_api）
  ├── mods/     — mod_registry.yml 引用 MOD 路径
  └── tools/    — rag 配置被 tools/rag/ 使用
```
