import { mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const DEFAULT_APP_URL = "http://localhost:5000/app/";
const DEFAULT_API_URL = "http://localhost:5000";
const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const SOURCE_PACK_DIR = resolve(SCRIPT_DIR, "..", "..", "examples", "story_packs", "a3_branching_quest");
const REPORT_DIR = resolve(SCRIPT_DIR, "..", "..", "test_runs", "long-playability");
const QUEST_ID = "unmask_the_salt_deal";
const REQUIRED_TRACE_STAGES = ["api.received", "nlu.parsed", "state.updated", "gm.rendered"];

const longRoute = [
  {
    action: "观察周围",
    location: "market_square",
    questStage: "gather_leads",
    flag: "observed_surroundings",
    actionType: "observe",
  },
  {
    action: "去旧码头",
    location: "smuggler_quay",
    questStage: "gather_leads",
    actionType: "move",
    locationChange: { from: "market_square", to: "smuggler_quay" },
  },
  {
    action: "检查网下盐箱",
    location: "smuggler_quay",
    questStage: "choose_approach",
    triggerId: "inspect_hidden_crate",
    flag: "hidden_crate_has_official_wax",
    actionType: "inspect",
    inventoryIncludes: ["salt_wax_rubbing", "a3_field_ration"],
  },
  {
    action: "使用专注技能",
    location: "smuggler_quay",
    questStage: "choose_approach",
    flag: "skill_focus_used",
    actionType: "skill",
    mpDelta: -3,
  },
  {
    action: "使用盐饼",
    location: "smuggler_quay",
    questStage: "choose_approach",
    actionType: "use_item",
    hpDelta: 4,
    consumedItemId: "a3_field_ration",
    inventoryExcludes: ["a3_field_ration"],
  },
  {
    action: "短暂休息",
    location: "smuggler_quay",
    questStage: "choose_approach",
    flag: "rested_recently",
    actionType: "rest",
    hpDelta: 1,
    mpDelta: 2,
  },
  {
    action: "与线人席舟交易",
    location: "smuggler_quay",
    questStage: "strike_quay_bargain",
    triggerId: "talk_runner_xi",
    flag: "branch_strike_quay_bargain",
    actionType: "talk",
    branchPath: "strike_quay_bargain",
    inventoryIncludes: ["quay_account_slip"],
    expectConsequence: true,
    responseIncludes: ["席舟", "暗线交易路线"],
  },
  {
    action: "复查封好的盐箱",
    location: "smuggler_quay",
    questStage: "seal_the_evidence",
    triggerId: "inspect_sealed_crate_after_bargain",
    flag: "quay_route_evidence_sealed",
    actionType: "inspect",
    branchPath: "strike_quay_bargain",
  },
  {
    action: "绕去封契库后门",
    location: "archive_hall",
    questStage: "seal_the_evidence",
    actionType: "move",
    branchPath: "strike_quay_bargain",
    locationChange: { from: "smuggler_quay", to: "archive_hall" },
  },
  {
    action: "回旧码头",
    location: "smuggler_quay",
    questStage: "seal_the_evidence",
    actionType: "move",
    branchPath: "strike_quay_bargain",
    locationChange: { from: "archive_hall", to: "smuggler_quay" },
  },
  {
    action: "绕去封契库后门",
    location: "archive_hall",
    questStage: "seal_the_evidence",
    actionType: "move",
    branchPath: "strike_quay_bargain",
    locationChange: { from: "smuggler_quay", to: "archive_hall" },
  },
  {
    action: "比对封契缺口",
    location: "archive_hall",
    questStage: "case_closed",
    questStatus: "completed",
    triggerId: "inspect_archive_seal",
    flag: "salt_contract_case_closed",
    actionType: "inspect",
    branchPath: "strike_quay_bargain",
    responseIncludes: ["私盐契案结案"],
  },
  {
    action: "看看周围",
    location: "archive_hall",
    questStage: "case_closed",
    questStatus: "completed",
    flag: "observed_surroundings",
    actionType: "observe",
    branchPath: "strike_quay_bargain",
  },
];

/**
 * 功能：断言长回合试玩条件，并保留清晰错误。
 * 入参：condition（boolean）：断言结果；message（string）：失败说明。
 * 出参：void。
 * 异常：condition 为 false 时抛出 Error，中断试玩。
 */
function assertPlaytest(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

/**
 * 功能：生成满足后端 ID 约束的临时标识。
 * 入参：prefix（string）：ID 前缀。
 * 出参：string，包含时间和随机后缀。
 * 异常：无。
 */
function makeId(prefix) {
  return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

/**
 * 功能：把 API 路径解析为 Flask 绝对 URL。
 * 入参：path（string）：以 / 开头的 API 路径。
 * 出参：string，绝对 URL。
 * 异常：URL 构造失败时由 URL 抛出 TypeError。
 */
function resolveApiUrl(path) {
  return new URL(path, process.env.LLMTRE_API_URL || DEFAULT_API_URL).toString();
}

/**
 * 功能：请求 JSON API，并返回状态码与响应体。
 * 入参：path（string）：API 路径；options（object）：method/body。
 * 出参：Promise<{status:number, body:object}>。
 * 异常：网络失败或 JSON 解析失败时向上抛出。
 */
async function requestApiJson(path, options = {}) {
  const response = await fetch(resolveApiUrl(path), {
    method: options.method ?? "GET",
    headers: { "Content-Type": "application/json" },
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const text = await response.text();
  return { status: response.status, body: text ? JSON.parse(text) : {} };
}

/**
 * 功能：读取 UTF-8 JSON 文件。
 * 入参：path（string）：目标文件路径。
 * 出参：Promise<object>，解析后的 JSON。
 * 异常：文件读取或 JSON 解析失败时向上抛出。
 */
async function readJson(path) {
  return JSON.parse(await readFile(path, "utf-8"));
}

/**
 * 功能：读取 Story Pack JSON 目录并按 ID 组织。
 * 入参：dir（string）：目录；idField（string）：对象 ID 字段。
 * 出参：Promise<object>，键为对象 ID。
 * 异常：文件读取、JSON 解析或 ID 缺失时抛出 Error。
 */
async function readJsonCollection(dir, idField) {
  const result = {};
  const entries = await readdir(dir, { withFileTypes: true });
  for (const entry of entries) {
    if (!entry.isFile() || !entry.name.endsWith(".json")) {
      continue;
    }
    const item = await readJson(join(dir, entry.name));
    const itemId = String(item[idField] ?? "").trim();
    assertPlaytest(itemId.length > 0, `${entry.name} 缺少 ${idField}`);
    result[itemId] = item;
  }
  return result;
}

/**
 * 功能：读取 Story Pack lore 文本集合。
 * 入参：dir（string）：lore 目录。
 * 出参：Promise<object>，键为文件名。
 * 异常：目录或文件读取失败时向上抛出。
 */
async function readLoreCollection(dir) {
  const result = {};
  const entries = await readdir(dir, { withFileTypes: true });
  for (const entry of entries) {
    if (entry.isFile()) {
      result[entry.name] = await readFile(join(dir, entry.name), "utf-8");
    }
  }
  return result;
}

/**
 * 功能：把 A3 示例包转换成外部导入 payload，并替换 pack_id。
 * 入参：packId（string）：临时 pack ID。
 * 出参：Promise<object>，可直接 POST 到 /api/story-packs。
 * 异常：源包文件缺失或格式错误时向上抛出。
 */
async function buildImportPayload(packId) {
  const manifest = await readJson(join(SOURCE_PACK_DIR, "manifest.json"));
  manifest.pack_id = packId;
  manifest.title = `${manifest.title}（长回合试玩）`;
  manifest.author = "TRE Long Playability";
  return {
    manifest,
    scenes: await readJsonCollection(join(SOURCE_PACK_DIR, "scenes"), "scene_id"),
    quests: await readJsonCollection(join(SOURCE_PACK_DIR, "quests"), "quest_id"),
    triggers: await readJsonCollection(join(SOURCE_PACK_DIR, "triggers"), "trigger_id"),
    lore: await readLoreCollection(join(SOURCE_PACK_DIR, "lore")),
  };
}

/**
 * 功能：在页面内安装 SSE 事件记录器，复制读取 /turns/stream 响应。
 * 入参：page（Page）：Playwright 页面。
 * 出参：Promise<void>。
 * 异常：注入失败时向上抛出；单条 SSE 解析失败仅写入 recorder_error。
 */
async function installSseRecorder(page) {
  await page.addInitScript(() => {
    window.__treSseEvents = [];
    const originalFetch = window.fetch.bind(window);
    window.fetch = async (...args) => {
      const response = await originalFetch(...args);
      const target =
        typeof args[0] === "string"
          ? args[0]
          : args[0] instanceof Request
            ? args[0].url
            : String(args[0] ?? "");
      if (target.includes("/turns/stream") && response.body) {
        response
          .clone()
          .text()
          .then((text) => {
            for (const block of text.split(/\n\n+/)) {
              const eventLine = block.split(/\n/).find((line) => line.startsWith("event:"));
              const dataLine = block.split(/\n/).find((line) => line.startsWith("data:"));
              if (!eventLine) {
                continue;
              }
              let payload = null;
              if (dataLine) {
                const rawData = dataLine.slice("data:".length).trim();
                try {
                  payload = rawData ? JSON.parse(rawData) : null;
                } catch (error) {
                  payload = {
                    parse_error: error instanceof Error ? error.message : String(error),
                    raw: rawData,
                  };
                }
              }
              window.__treSseEvents.push({
                event: eventLine.slice("event:".length).trim(),
                payload,
              });
            }
          })
          .catch((error) => {
            window.__treSseEvents.push({
              event: "recorder_error",
              payload: { message: error instanceof Error ? error.message : String(error) },
            });
          });
      }
      return response;
    };
  });
}

/**
 * 功能：读取页面中记录的 SSE 事件。
 * 入参：page（Page）：Playwright 页面。
 * 出参：Promise<object[]>，SSE 事件数组。
 * 异常：page.evaluate 失败时向上抛出。
 */
async function readRecordedSseEvents(page) {
  return await page.evaluate(() => window.__treSseEvents ?? []);
}

/**
 * 功能：通过浏览器同源上下文请求 JSON API。
 * 入参：page（Page）：Playwright 页面；path/options：API 路径和请求选项。
 * 出参：Promise<{status:number, body:object}>。
 * 异常：fetch 或 JSON 解析失败时向上抛出。
 */
async function requestJson(page, path, options = {}) {
  return await page.evaluate(
    async ({ urlPath, requestOptions }) => {
      const response = await fetch(urlPath, {
        method: requestOptions.method ?? "GET",
        headers: { "Content-Type": "application/json" },
        body: requestOptions.body ? JSON.stringify(requestOptions.body) : undefined,
      });
      const text = await response.text();
      return { status: response.status, body: text ? JSON.parse(text) : {} };
    },
    { urlPath: path, requestOptions: options }
  );
}

/**
 * 功能：读取物品 ID，兼容字符串和对象背包项。
 * 入参：item（unknown）：背包项。
 * 出参：string，无法识别时返回空字符串。
 * 异常：无。
 */
function readItemId(item) {
  if (item && typeof item === "object") {
    return String(item.item_id ?? item.id ?? item.name ?? "").trim();
  }
  return String(item ?? "").trim();
}

/**
 * 功能：把候选值规整为普通对象。
 * 入参：value（unknown）：候选对象。
 * 出参：object，非对象或 null 时返回空对象。
 * 异常：无。
 */
function asObject(value) {
  return value && typeof value === "object" ? value : {};
}

/**
 * 功能：把候选值规整为数组。
 * 入参：value（unknown）：候选数组。
 * 出参：unknown[]，非数组时返回空数组。
 * 异常：无。
 */
function asArray(value) {
  return Array.isArray(value) ? value : [];
}

/**
 * 功能：读取当前会话里的 A3 任务运行态。
 * 入参：sessionBody（object）：GET /api/sessions/{session_id} 响应。
 * 出参：object，缺少任务时返回空对象。
 * 异常：无。
 */
function readQuestProgress(sessionBody) {
  const sceneSnapshot = asObject(sessionBody.scene_snapshot);
  const quests = asArray(sceneSnapshot.active_quests);
  return quests.find((item) => item.quest_id === QUEST_ID) ?? {};
}

/**
 * 功能：读取角色背包 ID 列表，并保留后端原始 inventory_items。
 * 入参：activeCharacter（object）：active_character 响应片段。
 * 出参：object，包含 inventory 与 inventoryItems。
 * 异常：无。
 */
function readInventoryProgress(activeCharacter) {
  const inventoryItems = asArray(activeCharacter.inventory_items);
  const rawInventory = asArray(activeCharacter.inventory);
  const inventorySource = rawInventory.length > 0 ? rawInventory : inventoryItems;
  return {
    inventory: inventorySource.map(readItemId).filter(Boolean),
    inventoryItems,
  };
}

/**
 * 功能：从会话详情中提取长回合验收用状态。
 * 入参：sessionBody（object）：GET /api/sessions/{session_id} 响应。
 * 出参：object，包含位置、HP/MP、任务、背包、标记和分支。
 * 异常：不主动抛出；缺失字段按空值降级，调用方断言。
 */
function readProgress(sessionBody) {
  const activeCharacter = asObject(sessionBody.active_character);
  const sceneSnapshot = asObject(sessionBody.scene_snapshot);
  const quests = asArray(sceneSnapshot.active_quests);
  const quest = readQuestProgress(sessionBody);
  const inventory = readInventoryProgress(activeCharacter);
  return {
    location: activeCharacter.location ?? "",
    hp: Number(activeCharacter.hp ?? 0),
    maxHp: Number(activeCharacter.max_hp ?? 0),
    mp: Number(activeCharacter.mp ?? 0),
    maxMp: Number(activeCharacter.max_mp ?? 0),
    flags: asArray(activeCharacter.state_flags),
    inventory: inventory.inventory,
    inventoryItems: inventory.inventoryItems,
    questStatus: quest.status ?? "",
    questStage: quest.current_stage_id ?? "",
    branchPath: asObject(quest.data).branch_path ?? "",
    questData: asObject(quest.data),
    activeQuestCount: quests.length,
    recentMemory: String(sceneSnapshot.recent_memory ?? ""),
  };
}

/**
 * 功能：按 delta 与上下限计算预期资源值。
 * 入参：before（number）：变化前数值；delta（number）：差异；maxValue（number）：上限。
 * 出参：number，夹在 0..maxValue 内的结果。
 * 异常：无。
 */
function applyResourceDelta(before, delta, maxValue) {
  return Math.max(0, Math.min(maxValue, before + delta));
}

/**
 * 功能：读取回合 trace 阶段名。
 * 入参：turnBody（object）：回合响应或详情。
 * 出参：string[]，trace.stages 中的阶段名。
 * 异常：无；缺失 trace 时返回空数组。
 */
function readTraceStages(turnBody) {
  const stages = turnBody.trace?.stages ?? [];
  return Array.isArray(stages) ? stages.map((item) => item.stage).filter(Boolean) : [];
}

/**
 * 功能：在 /app 中选择临时 pack 并创建新会话。
 * 入参：page（Page）：页面；appUrl（string）：/app 地址；packId（string）：临时 pack。
 * 出参：Promise<string>，会话 ID。
 * 异常：页面加载、pack 列表刷新或创建超时时向上抛出。
 */
async function createSessionInApp(page, appUrl, packId) {
  await page.goto(appUrl, { waitUntil: "networkidle", timeout: 30_000 });
  await page.waitForFunction(
    (expectedPackId) =>
      [...document.querySelectorAll("select option")].some((item) => item.value === expectedPackId),
    packId,
    { timeout: 20_000 }
  );
  const sessionInput = page.getByLabel("输入或粘贴会话 ID");
  const previousSessionId = await sessionInput.inputValue().catch(() => "");
  await page.locator("select").first().selectOption({ value: packId });
  await page.getByRole("button", { name: /新会话/ }).click();
  await page.waitForFunction(
    (oldSessionId) => {
      const input = [...document.querySelectorAll("input")].find((item) =>
        item.value.startsWith("sess_")
      );
      return Boolean(input && input.value !== oldSessionId);
    },
    previousSessionId,
    { timeout: 30_000 }
  );
  return await sessionInput.inputValue();
}

/**
 * 功能：通过 /app 输入框提交 SSE 回合，并等待 done 与持久化完成。
 * 入参：page（Page）：页面；sessionId/actionText/expectedTotal：会话、行动、期望历史总数。
 * 出参：Promise<object>，包含 done 回合、详情、会话和 SSE 计数。
 * 异常：发送失败、SSE 未完成或历史未落库时抛出 Error。
 */
async function submitActionInApp(page, sessionId, actionText, expectedTotal) {
  const textbox = page.getByPlaceholder(/输入命令或对话/);
  await textbox.fill(actionText);
  await page.getByRole("button", { name: /^发送$/ }).click();

  const startedAt = Date.now();
  let lastHistory = null;
  while (Date.now() - startedAt < 120_000) {
    lastHistory = await requestJson(page, `/api/sessions/${sessionId}/turns?page=1&page_size=100`);
    const items = lastHistory.body.items ?? [];
    const latest = items[items.length - 1];
    if (
      lastHistory.body.total >= expectedTotal &&
      latest &&
      typeof latest.final_response === "string" &&
      latest.final_response.length > 0
    ) {
      await page.waitForFunction(
        (doneCount) =>
          (window.__treSseEvents ?? []).filter((event) => event.event === "done").length >=
          doneCount,
        expectedTotal,
        { timeout: 15_000 }
      );
      const detail = await requestJson(page, `/api/sessions/${sessionId}/turns/${latest.session_turn_id}`);
      const session = await requestJson(page, `/api/sessions/${sessionId}`);
      const sseEvents = await readRecordedSseEvents(page);
      const donePayloads = sseEvents
        .filter((event) => event.event === "done")
        .map((event) => event.payload)
        .filter(Boolean);
      return {
        turn: donePayloads[donePayloads.length - 1] ?? detail.body,
        persistedTurn: detail.body,
        session: session.body,
        sseDoneCount: donePayloads.length,
        sseEventCount: sseEvents.length,
      };
    }
    await page.waitForTimeout(1_000);
  }
  throw new Error(`等待长回合行动落库超时: ${actionText}; last=${JSON.stringify(lastHistory)}`);
}

/**
 * 功能：通过普通 JSON 回合路由提交行动，用于证明非 SSE 路径仍能反馈和持久化。
 * 入参：sessionId/actionText/expectedTotal：会话、行动、期望历史总数。
 * 出参：Promise<object>，包含普通回合响应、会话详情和历史。
 * 异常：HTTP 失败或历史未落库时抛出 Error。
 */
async function submitNormalAction(sessionId, actionText, expectedTotal) {
  const response = await requestApiJson(`/api/sessions/${sessionId}/turns`, {
    method: "POST",
    body: {
      request_id: makeId("long_normal_turn"),
      character_id: "player_01",
      sandbox_mode: false,
      user_input: actionText,
    },
  });
  assertPlaytest(response.status === 200, `普通回合失败: ${JSON.stringify(response)}`);
  const history = await requestApiJson(`/api/sessions/${sessionId}/turns?page=1&page_size=100`);
  assertPlaytest(
    history.status === 200 && history.body.total >= expectedTotal,
    `普通回合历史未落库: expected=${expectedTotal}, actual=${history.body.total}`
  );
  const session = await requestApiJson(`/api/sessions/${sessionId}`);
  assertPlaytest(session.status === 200, `普通回合后会话读取失败: ${JSON.stringify(session)}`);
  return { turn: response.body, session: session.body, history: history.body };
}

/**
 * 功能：构造单步断言上下文，避免多个断言 helper 重复读取回合状态。
 * 入参：step/result/before/index：步骤定义、回合结果、前置状态和序号。
 * 出参：object，包含回合、状态、差异和文本摘要。
 * 异常：无；缺失字段按空值降级，具体断言由后续 helper 负责。
 */
function buildStepAssertionContext(step, result, before, index) {
  const progress = readProgress(result.session);
  const turn = result.turn;
  const physics = asObject(turn.physics_diff);
  const actionIntent = asObject(turn.action_intent);
  const triggerIds = (turn.trigger_events ?? []).map((event) => event.trigger_id);
  const finalResponse = String(turn.final_response ?? "");
  return { step, result, before, index, progress, turn, physics, actionIntent, triggerIds, finalResponse };
}

/**
 * 功能：断言基础反馈、动作类型、位置和任务阶段。
 * 入参：context（object）：buildStepAssertionContext 返回值。
 * 出参：void。
 * 异常：基础状态不符时抛出 Error。
 */
function assertBasicStepState(context) {
  const { step, turn, actionIntent, progress, finalResponse, index } = context;
  assertPlaytest(finalResponse.length > 0, `第 ${index} 回合没有正常反馈`);
  assertPlaytest(turn.outcome === "valid_action", `第 ${index} 回合 outcome 错误: ${turn.outcome}`);
  assertPlaytest(actionIntent.type === step.actionType, `第 ${index} 回合动作类型错误: ${actionIntent.type}`);
  assertPlaytest(progress.location === step.location, `第 ${index} 回合位置错误: ${progress.location}`);
  assertPlaytest(progress.questStage === step.questStage, `第 ${index} 回合任务阶段错误: ${progress.questStage}`);
  assertPlaytest(progress.activeQuestCount > 0, `第 ${index} 回合缺少任务上下文`);
  if (step.questStatus) {
    assertPlaytest(progress.questStatus === step.questStatus, `第 ${index} 回合任务状态错误: ${progress.questStatus}`);
  }
}

/**
 * 功能：断言触发器、状态标记和背包包含/排除条件。
 * 入参：context（object）：单步断言上下文。
 * 出参：void。
 * 异常：触发器、标记或物品状态不符时抛出 Error。
 */
function assertStepInventoryAndFlags(context) {
  const { step, progress, physics, triggerIds, index } = context;
  if (step.triggerId) {
    assertPlaytest(triggerIds.includes(step.triggerId), `第 ${index} 回合缺少触发器: ${step.triggerId}`);
  }
  if (step.flag) {
    assertPlaytest(progress.flags.includes(step.flag), `第 ${index} 回合缺少状态标记: ${step.flag}`);
  }
  for (const itemId of step.inventoryIncludes ?? []) {
    assertPlaytest(progress.inventory.includes(itemId), `第 ${index} 回合背包缺少物品: ${itemId}`);
  }
  for (const itemId of step.inventoryExcludes ?? []) {
    assertPlaytest(!progress.inventory.includes(itemId), `第 ${index} 回合物品未被消耗: ${itemId}`);
  }
  if (step.consumedItemId) {
    assertPlaytest(physics.consumed_item_id === step.consumedItemId, `第 ${index} 回合 consumed_item_id 错误`);
  }
}

/**
 * 功能：断言 HP/MP 差异和上下限夹取后的最终值。
 * 入参：context（object）：单步断言上下文。
 * 出参：void。
 * 异常：资源差异或最终值不符时抛出 Error。
 */
function assertStepResourceDeltas(context) {
  const { step, before, progress, physics, index } = context;
  if (typeof step.mpDelta === "number") {
    assertPlaytest(physics.mp_delta === step.mpDelta, `第 ${index} 回合 mp_delta 错误: ${physics.mp_delta}`);
    assertPlaytest(
      progress.mp === applyResourceDelta(before.mp, step.mpDelta, before.maxMp),
      `第 ${index} 回合 MP 计算错误: before=${before.mp}, delta=${step.mpDelta}, actual=${progress.mp}`
    );
  }
  if (typeof step.hpDelta === "number") {
    assertPlaytest(physics.hp_delta === step.hpDelta, `第 ${index} 回合 hp_delta 错误: ${physics.hp_delta}`);
    assertPlaytest(
      progress.hp === applyResourceDelta(before.hp, step.hpDelta, before.maxHp),
      `第 ${index} 回合 HP 计算错误: before=${before.hp}, delta=${step.hpDelta}, actual=${progress.hp}`
    );
  }
}

/**
 * 功能：断言移动差异、分支路径和结构化分支后果。
 * 入参：context（object）：单步断言上下文。
 * 出参：void。
 * 异常：移动、分支或后果摘要不符时抛出 Error。
 */
function assertStepMovementAndBranch(context) {
  const { step, progress, physics, turn, index } = context;
  if (step.locationChange) {
    assertPlaytest(
      physics.location_change?.from === step.locationChange.from &&
        physics.location_change?.to === step.locationChange.to,
      `第 ${index} 回合移动差异错误: ${JSON.stringify(physics.location_change)}`
    );
  }
  if (step.branchPath) {
    assertPlaytest(progress.branchPath === step.branchPath, `第 ${index} 回合分支路线错误: ${progress.branchPath}`);
  }
  if (step.expectConsequence) {
    const consequences = turn.branch_consequences ?? [];
    assertPlaytest(consequences.length > 0, `第 ${index} 回合缺少分支后果摘要`);
    assertPlaytest(
      consequences[0].branch_path === step.branchPath,
      `第 ${index} 回合分支后果路线错误: ${JSON.stringify(consequences[0])}`
    );
    assertPlaytest(
      Array.isArray(consequences[0].state_changes) && consequences[0].state_changes.length >= 2,
      `第 ${index} 回合分支后果结构化变化不足`
    );
  }
}

/**
 * 功能：断言反馈关键文本和 TurnTrace 必要阶段。
 * 入参：context（object）：单步断言上下文。
 * 出参：void。
 * 异常：反馈或 trace 缺项时抛出 Error。
 */
function assertStepResponseAndTrace(context) {
  const { step, turn, finalResponse, index } = context;
  for (const text of step.responseIncludes ?? []) {
    assertPlaytest(finalResponse.includes(text), `第 ${index} 回合反馈缺少文本: ${text}`);
  }
  const traceStages = readTraceStages(turn);
  for (const required of REQUIRED_TRACE_STAGES) {
    assertPlaytest(traceStages.includes(required), `第 ${index} 回合 trace 缺少 ${required}`);
  }
}

/**
 * 功能：整理单步报告，供最终 JSON 记录验收证据。
 * 入参：context（object）：单步断言上下文。
 * 出参：object，步骤报告。
 * 异常：无。
 */
function buildStepReport(context) {
  const { step, result, progress, actionIntent, triggerIds, physics, finalResponse, index } = context;
  return {
    index,
    action: step.action,
    action_type: actionIntent.type,
    final_response: finalResponse.slice(0, 180),
    trigger_ids: triggerIds,
    physics_diff: physics,
    sse_done_count: result.sseDoneCount,
    sse_event_count: result.sseEventCount,
    ...progress,
  };
}

/**
 * 功能：断言单个回合满足长回合验收要求。
 * 入参：step/result/before/index：步骤定义、回合结果、前置状态和序号。
 * 出参：object，步骤报告。
 * 异常：断言失败时抛出 Error。
 */
function assertStep(step, result, before, index) {
  const context = buildStepAssertionContext(step, result, before, index);
  assertBasicStepState(context);
  assertStepInventoryAndFlags(context);
  assertStepResourceDeltas(context);
  assertStepMovementAndBranch(context);
  assertStepResponseAndTrace(context);
  return buildStepReport(context);
}

/**
 * 功能：核验长回合历史、记忆摘要和页面恢复状态。
 * 入参：page/sessionId/expectedTurnCount/report：页面、会话、回合数和报告对象。
 * 出参：Promise<void>。
 * 异常：历史、记忆或恢复断言失败时抛出 Error。
 */
async function assertHistoryMemoryAndRestore(page, sessionId, expectedTurnCount, report) {
  const history = await requestJson(page, `/api/sessions/${sessionId}/turns?page=1&page_size=100`);
  assertPlaytest(history.body.total >= expectedTurnCount, `历史回合数不足: ${history.body.total}`);
  const inputs = (history.body.items ?? []).map((item) => item.user_input);
  for (const step of longRoute) {
    assertPlaytest(inputs.includes(step.action), `历史缺少玩家行动: ${step.action}`);
  }
  const memory = await requestJson(page, `/api/sessions/${sessionId}/memory?format=raw`);
  assertPlaytest(memory.status === 200, `记忆读取失败: ${JSON.stringify(memory)}`);
  assertPlaytest(
    Array.isArray(memory.body.recent_turns) && memory.body.recent_turns.length >= 5,
    `记忆 recent_turns 不完整: ${JSON.stringify(memory.body.recent_turns)}`
  );
  const memoryText = memory.body.recent_turns.map((item) => String(item.text ?? "")).join("\n");
  for (const action of ["观察周围", "使用专注技能", "使用盐饼", "与线人席舟交易", "等待片刻"]) {
    assertPlaytest(memoryText.includes(action), `记忆摘要缺少关键行动: ${action}`);
  }
  report.memory_recent_turns = memory.body.recent_turns.length;
  report.memory_token_estimate = memory.body.token_estimate;

  await page.reload({ waitUntil: "networkidle", timeout: 30_000 });
  await page.getByLabel("输入或粘贴会话 ID").fill(sessionId);
  await page.getByRole("button", { name: /加载 \/ 保存/ }).click();
  await page.waitForFunction(
    () => document.body.innerText.includes("私盐契结案") && document.body.innerText.includes("盐契分岔"),
    null,
    { timeout: 30_000 }
  );
  const restored = await requestJson(page, `/api/sessions/${sessionId}`);
  const restoredProgress = readProgress(restored.body);
  assertPlaytest(
    restoredProgress.questStage === "case_closed" && restoredProgress.questStatus === "completed",
    `恢复后任务状态丢失: ${JSON.stringify(restoredProgress)}`
  );
}

/**
 * 功能：创建长回合报告初始结构。
 * 入参：packId（string）：临时 Story Pack ID。
 * 出参：object，报告草稿。
 * 异常：无。
 */
function createLongPlaytestReport(packId) {
  return {
    ok: false,
    pack_id: packId,
    imported: false,
    pack_deleted: false,
    session_id: "",
    steps: [],
    normal_turn: null,
    coverage: {},
    console_records: [],
    page_errors: [],
    screenshot_path: "",
    report_path: "",
  };
}

/**
 * 功能：导入临时 A3 Story Pack，供真实 /app 长回合试玩使用。
 * 入参：packId（string）：临时 pack ID；report（object）：报告草稿。
 * 出参：Promise<void>。
 * 异常：导入失败时抛出 Error。
 */
async function importTemporaryPack(packId, report) {
  const importResult = await requestApiJson("/api/story-packs", {
    method: "POST",
    body: await buildImportPayload(packId),
  });
  assertPlaytest(importResult.status === 201, `长回合 pack 导入失败: ${JSON.stringify(importResult)}`);
  report.imported = true;
}

/**
 * 功能：创建浏览器页面并安装控制台、页面错误和 SSE 记录钩子。
 * 入参：browser（Browser）：Playwright 浏览器；report（object）：报告草稿。
 * 出参：Promise<Page>。
 * 异常：页面创建或 recorder 注入失败时向上抛出。
 */
async function createInstrumentedPage(browser, report) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 980 } });
  page.on("console", (message) => {
    if (["error", "warning"].includes(message.type())) {
      report.console_records.push({
        type: message.type(),
        text: message.text(),
        location: message.location(),
      });
    }
  });
  page.on("pageerror", (error) => {
    report.page_errors.push(error instanceof Error ? error.message : String(error));
  });
  await installSseRecorder(page);
  return page;
}

/**
 * 功能：创建会话并断言长回合起点状态。
 * 入参：page/appUrl/packId/report：页面、入口、临时包和报告草稿。
 * 出参：Promise<object>，起点状态。
 * 异常：建会话失败或起点状态不符时抛出 Error。
 */
async function createLongPlaytestSession(page, appUrl, packId, report) {
  report.session_id = await createSessionInApp(page, appUrl, packId);
  const session = await requestJson(page, `/api/sessions/${report.session_id}`);
  const progress = readProgress(session.body);
  assertPlaytest(progress.location === "market_square", `起点位置错误: ${progress.location}`);
  assertPlaytest(progress.questStage === "gather_leads", `起始任务阶段错误: ${progress.questStage}`);
  return progress;
}

/**
 * 功能：依次执行长回合 SSE 路线，并把每步状态证据写入报告。
 * 入参：page（Page）：页面；report（object）：报告；before（object）：起点状态。
 * 出参：Promise<void>。
 * 异常：任一步回合或断言失败时抛出 Error。
 */
async function runLongRouteSteps(page, report, before) {
  let current = before;
  for (const [index, step] of longRoute.entries()) {
    const result = await submitActionInApp(page, report.session_id, step.action, index + 1);
    report.steps.push(assertStep(step, result, current, index + 1));
    current = readProgress(result.session);
  }
}

/**
 * 功能：断言页面展示了玩家可读分支后果与物品名。
 * 入参：page（Page）：页面。
 * 出参：Promise<void>。
 * 异常：页面缺少关键展示文本时抛出 Error。
 */
async function assertLongPlaytestPageText(page) {
  const bodyText = await page.locator("body").innerText();
  assertPlaytest(bodyText.includes("选择后果"), "页面未展示分支选择后果");
  assertPlaytest(
    bodyText.includes("现场盐饼") || !bodyText.includes("a3_field_ration"),
    "页面物品展示未使用玩家可读名称"
  );
}

/**
 * 功能：提交并断言普通 JSON 回合，证明非 SSE 路径仍与流式路径等价。
 * 入参：report（object）：报告草稿。
 * 出参：Promise<void>。
 * 异常：普通回合解析、状态或落库不符时抛出 Error。
 */
async function assertNormalTurnPath(report) {
  const normal = await submitNormalAction(report.session_id, "等待片刻", longRoute.length + 1);
  const progress = readProgress(normal.session);
  assertPlaytest(normal.turn.outcome === "valid_action", `普通回合 outcome 错误: ${normal.turn.outcome}`);
  assertPlaytest(normal.turn.action_intent?.type === "wait", "普通回合未解析为 wait");
  assertPlaytest(
    (normal.turn.physics_diff?.state_flags_add ?? []).includes("waited_recently"),
    "普通回合缺少等待状态标记"
  );
  assertPlaytest(progress.location === "archive_hall", "普通回合后位置不应改变");
  report.normal_turn = {
    action: "等待片刻",
    outcome: normal.turn.outcome,
    action_type: normal.turn.action_intent?.type,
    physics_diff: normal.turn.physics_diff,
    final_response: String(normal.turn.final_response ?? "").slice(0, 180),
    location: progress.location,
    quest_stage: progress.questStage,
  };
}

/**
 * 功能：截图并断言页面运行期没有 error 级异常。
 * 入参：page（Page）：页面；report（object）：报告草稿。
 * 出参：Promise<void>。
 * 异常：页面 error 或控制台 error 存在时抛出 Error。
 */
async function assertPageHealthAndScreenshot(page, report) {
  report.screenshot_path = resolve(REPORT_DIR, `long-playability-${report.session_id}.png`);
  await page.screenshot({ path: report.screenshot_path, fullPage: true });
  assertPlaytest(report.page_errors.length === 0, `页面异常: ${JSON.stringify(report.page_errors)}`);
  assertPlaytest(
    report.console_records.filter((item) => item.type === "error").length === 0,
    `控制台 error: ${JSON.stringify(report.console_records)}`
  );
}

/**
 * 功能：生成并断言长回合覆盖率摘要。
 * 入参：report（object）：报告草稿。
 * 出参：void。
 * 异常：关键交互类型或场景覆盖不足时抛出 Error。
 */
function finalizeCoverage(report) {
  report.coverage = {
    streamed_turns: longRoute.length,
    normal_turns: 1,
    total_turns: longRoute.length + 1,
    scenes: [...new Set(report.steps.map((step) => step.location))],
    npc_turns: report.steps.filter((step) => step.action_type === "talk").length,
    item_turns: report.steps.filter((step) => step.action_type === "use_item").length,
    skill_turns: report.steps.filter((step) => step.action_type === "skill").length,
    movement_turns: report.steps.filter((step) => step.action_type === "move").length,
    final_quest_status: report.steps[report.steps.length - 1].questStatus,
    final_quest_stage: report.steps[report.steps.length - 1].questStage,
  };
  assertPlaytest(report.coverage.total_turns >= 12, "长回合试玩回合数不足");
  assertPlaytest(report.coverage.scenes.length >= 3, "自由移动未覆盖 3 个场景");
  assertPlaytest(report.coverage.npc_turns >= 1, "缺少 NPC 交互回合");
  assertPlaytest(report.coverage.item_turns >= 1, "缺少物品使用回合");
  assertPlaytest(report.coverage.skill_turns >= 1, "缺少技能使用回合");
  assertPlaytest(report.coverage.movement_turns >= 4, "自由移动覆盖不足");
  assertPlaytest(report.coverage.final_quest_status === "completed", "长回合任务未完成");
}

/**
 * 功能：删除临时 pack 并记录删除状态。
 * 入参：packId（string）：临时 pack ID；report（object）：报告草稿。
 * 出参：Promise<void>。
 * 异常：删除失败时抛出 Error。
 */
async function deleteTemporaryPack(packId, report) {
  const deleteResult = await requestApiJson(`/api/story-packs/${packId}`, { method: "DELETE" });
  assertPlaytest(deleteResult.status === 200, `临时 pack 删除失败: ${JSON.stringify(deleteResult)}`);
  report.pack_deleted = true;
}

/**
 * 功能：执行完整长回合可玩性试玩。
 * 入参：无；LLMTRE_APP_URL/LLMTRE_API_URL 可覆盖入口。
 * 出参：Promise<object>，包含逐回合证据、截图和报告路径。
 * 异常：任一验收项失败时向上抛出。
 */
async function runPlaytest() {
  const appUrl = process.env.LLMTRE_APP_URL || DEFAULT_APP_URL;
  const packId = makeId("long_playability_a3");
  const report = createLongPlaytestReport(packId);
  let browser = null;
  try {
    await mkdir(REPORT_DIR, { recursive: true });
    await importTemporaryPack(packId, report);
    browser = await chromium.launch({ headless: true });
    const page = await createInstrumentedPage(browser, report);
    const before = await createLongPlaytestSession(page, appUrl, packId, report);
    await runLongRouteSteps(page, report, before);
    await assertLongPlaytestPageText(page);
    await assertNormalTurnPath(report);
    await assertHistoryMemoryAndRestore(page, report.session_id, longRoute.length + 1, report);
    await assertPageHealthAndScreenshot(page, report);
    finalizeCoverage(report);
    await deleteTemporaryPack(packId, report);
    report.ok = true;
    report.report_path = resolve(REPORT_DIR, `long-playability-${packId}.json`);
    await writeFile(report.report_path, JSON.stringify(report, null, 2), "utf-8");
    await page.close();
    return report;
  } finally {
    if (browser) {
      await browser.close();
    }
    if (report.imported && !report.pack_deleted) {
      await requestApiJson(`/api/story-packs/${packId}`, { method: "DELETE" }).catch(() => {});
    }
  }
}

runPlaytest()
  .then((report) => {
    console.log("LONG_PLAYABILITY_PLAYTEST_OK");
    console.log(JSON.stringify(report, null, 2));
  })
  .catch((error) => {
    console.error("LONG_PLAYABILITY_PLAYTEST_FAILED");
    console.error(error instanceof Error ? error.stack : String(error));
    process.exitCode = 1;
  });
