# TRE 子模块详解：core

## 模块定位

`core/` 是 TRE 引擎的**基础设施层**，位于四层架构中「逻辑层」的底部，为上层所有模块提供两个核心能力：

- **中央事件总线（EventBus）**：带冲突检测的钩子分发与执行引擎，所有状态写入须经过事件总线事务拦截器
- **运行日志系统（runtime_logging）**：关键模块的分离文件日志基础设施

## 目录与文件

```
core/
├── __init__.py            # 空包，仅标记为 Python 包
├── event_bus.py           # EventBus + HookContext
└── runtime_logging.py     # ensure_runtime_logging()
```

## 关键实现

### 1. EventBus（`event_bus.py`）

**核心机制**：从 `config/mod_registry.yml` 读取已启用 MOD 的钩子清单，按优先级排序后缓存。当 `emit(event_name, state)` 被调用时，按序执行匹配的钩子函数。

```python
@dataclass
class HookContext:
    event_name: str
    state: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    locked_paths: set[str] = field(default_factory=set)
```

关键 API：

| 方法 | 功能 |
|------|------|
| `__init__(registry_path, mods_root)` | 初始化，调用 `_refresh_hooks_cache()` |
| `_refresh_hooks_cache()` | 从 mod_registry.yml 加载钩子，按 priority 排序 |
| `emit(event_name, state)` | 同步触发事件：遍历钩子 → 加载脚本 → 调用 hook(state, ctx) |
| `reload()` | 重新加载注册表（运行时热更新） |

**事件模型**：

- `emit()` 是同步调用，所有钩子执行完毕后返回最终 `state`
- 钩子执行顺序由 MOD 的 `priority` 控制（降序，高优先先执行）
- 预设 stop propagation 常量 `STOP_PROPAGATION`，钩子返回该值可终止后续钩子
- 每个钩子函数签名：`hook(state: dict, ctx: HookContext) -> dict | None`

**冲突检测**：

- `HookContext.locked_paths` 记录当前事件中已被写入的字段路径
- 当钩子尝试写入已被锁定的路径时，事件总线根据 `conflict_strategy` 处理：

| 策略 | 行为 |
|------|------|
| `smart_merge` | 默认。低优先级钩子写入高优先级钩子已锁路径 → 丢弃并告警 |
| `overwrite` | 后执行的钩子覆盖前者 |
| `reject` | 已被锁定的写入全部拒绝 |

**脚本加载**：

- 每个钩子指向实际 MOD 目录下的 `.py` 脚本
- 通过 `importlib.util.spec_from_file_location()` 动态加载
- 脚本不存在或加载失败时记录告警，不阻断事件总线

**降级设计**：

- `mod_registry.yml` 不存在 → 空载运行，不抛异常
- 钩子脚本执行异常 → 捕获后记录错误，继续执行后续钩子
- 无钩子注册的事件 → emit() 直接返回原 state

### 2. Runtime Logging（`runtime_logging.py`）

将三个关键模块的日志写入独立的文件，便于排查：

| Logger Name | 文件 | 用途 |
|---|---|---|
| `Workflow.MainLoop` | `logs/main_loop.log` | 主循环各阶段记录 |
| `EventBus` | `logs/event_bus.log` | 事件分发与冲突记录 |
| `Workflow.AsyncWatchers` | `logs/outer_loop.log` | 外环事件投递记录 |

实现要点：

- 初始化幂等：`_INITIALIZED` 全局标志确保只设置一次
- 自动创建 `logs/` 目录（`mkdir(parents=True, exist_ok=True)`）
- 只读文件系统降级：`mkdir` 失败时挂 `NullHandler`，不阻断主流程
- `_attach_file_handler()` 去重检查：同一文件路径不重复添加 handler

## 设计模式

| 模式 | 实现 |
|------|------|
| **观察者 / 事件驱动** | EventBus 作为中心事件调度器，MOD 钩子订阅特定事件名 |
| **策略模式** | conflict_strategy（smart_merge / overwrite / reject） |
| **插件加载** | importlib 动态加载 MOD 钩子脚本 |
| **单例初始化** | runtime_logging._INITIALIZED 确保只初始化一次 |

## 四层架构中的位置

```
智能层  ← agents/
逻辑层  ← core/、game_workflows/、web_api/
          ├── core/            ← 基础设施（事件总线 + 日志）
          ├── game_workflows/  ← 主循环编排
          └── web_api/         ← HTTP 入口
持久层  ← state/
资源层  ← config/, mods/, docs/
```

`core/` 是整个引擎的「骨架」，被 `web_api/service.py`（初始化）、`game_workflows/`（运行期事件）、`mods/`（钩子注册）三方共同依赖。
