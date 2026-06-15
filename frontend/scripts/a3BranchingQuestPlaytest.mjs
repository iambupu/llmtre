import { mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const DEFAULT_APP_URL = "http://localhost:5000/app/";
const DEFAULT_API_URL = "http://localhost:5000";
const SOURCE_PACK_DIR = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
  "examples",
  "story_packs",
  "a3_branching_quest"
);
const REPORT_DIR = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
  "test_runs",
  "a3-branching"
);
const QUEST_ID = "unmask_the_salt_deal";
const EXPECTED_TRACE_STAGES = ["api.received", "pack.runtime", "state.updated", "gm.rendered"];

const routes = {
  public: [
    {
      action: "检查潮湿告示",
      location: "market_square",
      questStage: "gather_leads",
      triggerId: "inspect_market_notice",
      flag: "market_notice_date_mismatch",
    },
    {
      action: "去旧码头",
      location: "smuggler_quay",
      questStage: "gather_leads",
    },
    {
      action: "检查网下盐箱",
      location: "smuggler_quay",
      questStage: "choose_approach",
      triggerId: "inspect_hidden_crate",
      flag: "hidden_crate_has_official_wax",
      itemId: "salt_wax_rubbing",
    },
    {
      action: "去守备所",
      location: "watch_house",
      questStage: "choose_approach",
    },
    {
      action: "向云校尉公开证据",
      location: "watch_house",
      questStage: "report_to_watch",
      triggerId: "talk_captain_yun",
      flag: "branch_report_to_watch",
      branchPath: "report_to_watch",
      expectConsequence: true,
    },
    {
      action: "检查守备案板",
      location: "watch_house",
      questStage: "seal_the_evidence",
      triggerId: "inspect_caseboard_after_report",
      flag: "watch_route_evidence_sealed",
    },
    {
      action: "进入封契库",
      location: "archive_hall",
      questStage: "seal_the_evidence",
    },
    {
      action: "比对封契缺口",
      location: "archive_hall",
      questStage: "case_closed",
      questStatus: "completed",
      triggerId: "inspect_archive_seal",
      flag: "salt_contract_case_closed",
    },
  ],
  private: [
    {
      action: "检查潮湿告示",
      location: "market_square",
      questStage: "gather_leads",
      triggerId: "inspect_market_notice",
      flag: "market_notice_date_mismatch",
    },
    {
      action: "去旧码头",
      location: "smuggler_quay",
      questStage: "gather_leads",
    },
    {
      action: "检查网下盐箱",
      location: "smuggler_quay",
      questStage: "choose_approach",
      triggerId: "inspect_hidden_crate",
      flag: "hidden_crate_has_official_wax",
      itemId: "salt_wax_rubbing",
    },
    {
      action: "与线人席舟交易",
      location: "smuggler_quay",
      questStage: "strike_quay_bargain",
      triggerId: "talk_runner_xi",
      flag: "branch_strike_quay_bargain",
      branchPath: "strike_quay_bargain",
      itemId: "quay_account_slip",
      expectConsequence: true,
    },
    {
      action: "复查封好的盐箱",
      location: "smuggler_quay",
      questStage: "seal_the_evidence",
      triggerId: "inspect_sealed_crate_after_bargain",
      flag: "quay_route_evidence_sealed",
    },
    {
      action: "绕去封契库后门",
      location: "archive_hall",
      questStage: "seal_the_evidence",
    },
    {
      action: "比对封契缺口",
      location: "archive_hall",
      questStage: "case_closed",
      questStatus: "completed",
      triggerId: "inspect_archive_seal",
      flag: "salt_contract_case_closed",
    },
  ],
};

/**
 * 功能：断言 A3 试玩条件并输出稳定错误信息。
 * 入参：condition（boolean）：断言结果；message（string）：失败说明。
 * 出参：void。
 * 异常：condition 为 false 时抛出 Error，中断本次 A3 试玩。
 */
function assertPlaytest(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

/**
 * 功能：生成只包含小写字母、数字和下划线的临时 ID。
 * 入参：prefix（string）：业务前缀。
 * 出参：string，满足后端 pack_id/request_id 约束。
 * 异常：无。
 */
function makeId(prefix) {
  return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

/**
 * 功能：把 API 相对路径解析为 Flask 直连 URL。
 * 入参：path（string）：以 /api 开头的路径。
 * 出参：string，绝对 URL。
 * 异常：URL 构造失败时由 URL 抛出 TypeError。
 */
function resolveApiUrl(path) {
  return new URL(path, process.env.LLMTRE_API_URL || DEFAULT_API_URL).toString();
}

/**
 * 功能：通过 Node fetch 请求 Flask JSON API，保留统一响应体。
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
 * 入参：path（string）：目标 JSON 路径。
 * 出参：Promise<object>，解析后的 JSON。
 * 异常：文件读取或 JSON 解析失败时向上抛出。
 */
async function readJson(path) {
  return JSON.parse(await readFile(path, "utf-8"));
}

/**
 * 功能：读取 Story Pack JSON 集合目录，按对象 ID 组成导入 payload 字段。
 * 入参：dir（string）：集合目录；idField（string）：对象 ID 字段名。
 * 出参：Promise<object>，键为对象 ID，值为 JSON 对象。
 * 异常：目录读取、JSON 解析或 ID 缺失时抛出 Error。
 */
async function readJsonCollection(dir, idField) {
  const items = {};
  const entries = await readdir(dir, { withFileTypes: true });
  for (const entry of entries) {
    if (!entry.isFile() || !entry.name.endsWith(".json")) {
      continue;
    }
    const item = await readJson(join(dir, entry.name));
    const itemId = String(item[idField] ?? "").trim();
    assertPlaytest(itemId.length > 0, `${entry.name} 缺少 ${idField}`);
    items[itemId] = item;
  }
  return items;
}

/**
 * 功能：读取 Story Pack lore 文本集合。
 * 入参：dir（string）：lore 目录。
 * 出参：Promise<object>，键为文件名，值为 Markdown 文本。
 * 异常：目录读取或文件读取失败时向上抛出。
 */
async function readLoreCollection(dir) {
  const items = {};
  const entries = await readdir(dir, { withFileTypes: true });
  for (const entry of entries) {
    if (entry.isFile()) {
      items[entry.name] = await readFile(join(dir, entry.name), "utf-8");
    }
  }
  return items;
}

/**
 * 功能：把 A3 示例包转换为外部上传 payload，并替换 pack_id 避免污染源包。
 * 入参：packId（string）：临时外部 pack ID。
 * 出参：Promise<object>，可直接 POST 到 /api/story-packs。
 * 异常：源 pack 文件缺失、格式错误或资源读取失败时向上抛出。
 */
async function buildImportPayload(packId) {
  const manifest = await readJson(join(SOURCE_PACK_DIR, "manifest.json"));
  manifest.pack_id = packId;
  manifest.title = `${manifest.title}（A3 端到端试玩）`;
  manifest.author = "TRE A3 Playtest";
  return {
    manifest,
    scenes: await readJsonCollection(join(SOURCE_PACK_DIR, "scenes"), "scene_id"),
    quests: await readJsonCollection(join(SOURCE_PACK_DIR, "quests"), "quest_id"),
    triggers: await readJsonCollection(join(SOURCE_PACK_DIR, "triggers"), "trigger_id"),
    lore: await readLoreCollection(join(SOURCE_PACK_DIR, "lore")),
  };
}

/**
 * 功能：通过浏览器同源上下文请求 JSON API。
 * 入参：page（Page）：Playwright 页面；path（string）：API 路径；options（object）：method/body。
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
 * 功能：在页面内安装 SSE 记录器，复制读取 /turns/stream 响应。
 * 入参：page（Page）：Playwright 页面。
 * 出参：Promise<void>。
 * 异常：addInitScript 注入失败时向上抛出；解析失败只记录 recorder_error。
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
              const lines = block.split(/\n/);
              const eventLine = lines.find((line) => line.startsWith("event:"));
              const dataLine = lines.find((line) => line.startsWith("data:"));
              if (!eventLine) {
                continue;
              }
              const event = eventLine.slice("event:".length).trim();
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
              window.__treSseEvents.push({ event, payload });
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
 * 出参：Promise<object[]>，按捕获顺序排列的 SSE 事件。
 * 异常：page.evaluate 失败时向上抛出。
 */
async function readRecordedSseEvents(page) {
  return await page.evaluate(() => window.__treSseEvents ?? []);
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
 * 入参：sessionBody（object）：GET /api/sessions/{session_id} 响应主体。
 * 出参：object，缺少任务时返回空对象。
 * 异常：无。
 */
function readQuestProgress(sessionBody) {
  const sceneSnapshot = asObject(sessionBody.scene_snapshot);
  return asArray(sceneSnapshot.active_quests).find((item) => item.quest_id === QUEST_ID) ?? {};
}

/**
 * 功能：从会话详情提取 A3 任务、位置、角色标记和背包。
 * 入参：sessionBody（object）：GET /api/sessions/{session_id} 响应主体。
 * 出参：object，包含 location、flags、inventory、questStatus、questStage、branchPath。
 * 异常：不主动抛出；缺失字段按空值降级，调用方断言。
 */
function readSessionProgress(sessionBody) {
  const activeCharacter = asObject(sessionBody.active_character);
  const quest = readQuestProgress(sessionBody);
  return {
    location: activeCharacter.location ?? "",
    flags: asArray(activeCharacter.state_flags),
    inventory: asArray(activeCharacter.inventory),
    questStatus: quest.status ?? "",
    questStage: quest.current_stage_id ?? "",
    branchPath: asObject(quest.data).branch_path ?? "",
    questData: asObject(quest.data),
  };
}

/**
 * 功能：从物品对象或字符串中读取 item_id。
 * 入参：item（unknown）：背包条目。
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
 * 功能：在 /app 中选择临时 A3 pack 并创建新会话。
 * 入参：page（Page）：Playwright 页面；appUrl（string）：/app 地址；packId（string）：临时 pack。
 * 出参：Promise<string>，新建会话 ID。
 * 异常：页面加载、pack 选项刷新或会话创建超时时向上抛出。
 */
async function createSessionInApp(page, appUrl, packId) {
  await page.goto(appUrl, { waitUntil: "networkidle", timeout: 30_000 });
  await page.waitForFunction(
    (expectedPackId) =>
      [...document.querySelectorAll("select option")].some(
        (item) => item.value === expectedPackId
      ),
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
 * 功能：用玩家输入框提交行动，并等待 SSE done、历史和会话状态落库。
 * 入参：page（Page）：Playwright 页面；sessionId/actionText/expectedTotal：会话、行动和回合数。
 * 出参：Promise<object>，包含 done payload、持久化回合、会话详情和 SSE 计数。
 * 异常：按钮不可用、SSE 未完成或历史未落库时抛出 Error。
 */
async function submitAction(page, sessionId, actionText, expectedTotal) {
  const textbox = page.getByPlaceholder(/输入命令或对话/);
  await textbox.fill(actionText);
  await page.getByRole("button", { name: /^发送$/ }).click();

  const startedAt = Date.now();
  let lastHistory = null;
  while (Date.now() - startedAt < 120_000) {
    lastHistory = await requestJson(
      page,
      `/api/sessions/${sessionId}/turns?page=1&page_size=100`
    );
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
        { timeout: 10_000 }
      );
      const turnDetail = await requestJson(
        page,
        `/api/sessions/${sessionId}/turns/${latest.session_turn_id}`
      );
      const sessionDetail = await requestJson(page, `/api/sessions/${sessionId}`);
      const sseEvents = await readRecordedSseEvents(page);
      const donePayloads = sseEvents
        .filter((event) => event.event === "done")
        .map((event) => event.payload)
        .filter(Boolean);
      return {
        turn: donePayloads[donePayloads.length - 1] ?? turnDetail.body,
        persistedTurn: turnDetail.body,
        session: sessionDetail.body,
        sseEventCount: sseEvents.length,
        sseDoneCount: donePayloads.length,
      };
    }
    await page.waitForTimeout(1_000);
  }
  throw new Error(`等待 A3 回合 ${expectedTotal} 完成超时: ${JSON.stringify(lastHistory)}`);
}

/**
 * 功能：读取回合 trace 阶段名。
 * 入参：turnBody（object）：回合响应或持久化回合详情。
 * 出参：string[]，trace.stages 中的阶段名。
 * 异常：无；缺失 trace 时返回空数组。
 */
function readTraceStages(turnBody) {
  const stages = turnBody.trace?.stages ?? [];
  return Array.isArray(stages) ? stages.map((item) => item.stage).filter(Boolean) : [];
}

/**
 * 功能：断言单回合后的位置、任务阶段、触发器、标记、物品和后果摘要。
 * 入参：routeName/step/result/index：路线名、步骤定义、回合结果和序号。
 * 出参：object，整理后的步骤报告。
 * 异常：断言失败时抛出 Error。
 */
function assertStep(routeName, step, result, index) {
  const progress = readSessionProgress(result.session);
  const triggerIds = (result.turn.trigger_events ?? []).map((event) => event.trigger_id);
  const branchConsequences = result.turn.branch_consequences ?? [];
  assertPlaytest(
    progress.location === step.location,
    `${routeName} 第 ${index} 回合位置错误: expected=${step.location}, actual=${progress.location}`
  );
  assertPlaytest(
    progress.questStage === step.questStage,
    `${routeName} 第 ${index} 回合任务阶段错误: expected=${step.questStage}, actual=${progress.questStage}`
  );
  if (step.questStatus) {
    assertPlaytest(
      progress.questStatus === step.questStatus,
      `${routeName} 第 ${index} 回合任务状态错误: expected=${step.questStatus}, actual=${progress.questStatus}`
    );
  }
  if (step.triggerId) {
    assertPlaytest(
      triggerIds.includes(step.triggerId),
      `${routeName} 第 ${index} 回合缺少触发器: ${step.triggerId}`
    );
  }
  if (step.flag) {
    assertPlaytest(
      progress.flags.includes(step.flag),
      `${routeName} 第 ${index} 回合缺少状态标记: ${step.flag}`
    );
  }
  if (step.itemId) {
    const itemIds = progress.inventory.map(readItemId);
    assertPlaytest(
      itemIds.includes(step.itemId),
      `${routeName} 第 ${index} 回合背包缺少物品: ${step.itemId}`
    );
  }
  if (step.branchPath) {
    assertPlaytest(
      progress.branchPath === step.branchPath,
      `${routeName} 第 ${index} 回合 branch_path 错误: expected=${step.branchPath}, actual=${progress.branchPath}`
    );
  }
  if (step.expectConsequence) {
    assertPlaytest(
      branchConsequences.length > 0,
      `${routeName} 第 ${index} 回合缺少 branch_consequences`
    );
    const consequence = branchConsequences[0];
    assertPlaytest(
      consequence.branch_path === step.branchPath,
      `${routeName} 第 ${index} 回合后果分支错误: ${JSON.stringify(consequence)}`
    );
    assertPlaytest(
      Array.isArray(consequence.state_changes) && consequence.state_changes.length >= 2,
      `${routeName} 第 ${index} 回合后果变化不足 2 类`
    );
  }
  return {
    index,
    action: step.action,
    trigger_ids: triggerIds,
    branch_consequence_count: branchConsequences.length,
    sse_done_count: result.sseDoneCount,
    final_response: String(result.turn.final_response ?? "").slice(0, 160),
    ...progress,
  };
}

/**
 * 功能：创建路线试玩页面并安装控制台、页面异常和 SSE 记录钩子。
 * 入参：browser（Browser）：Playwright 浏览器。
 * 出参：Promise<object>，包含 page、consoleRecords、pageErrors。
 * 异常：页面创建或 recorder 注入失败时向上抛出。
 */
async function createRoutePage(browser) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  const consoleRecords = [];
  const pageErrors = [];
  page.on("console", (message) => {
    if (["error", "warning"].includes(message.type())) {
      consoleRecords.push({
        type: message.type(),
        text: message.text(),
        location: message.location(),
      });
    }
  });
  page.on("pageerror", (error) => {
    pageErrors.push(error instanceof Error ? error.message : String(error));
  });
  await installSseRecorder(page);
  return { page, consoleRecords, pageErrors };
}

/**
 * 功能：创建路线会话并断言初始 A3 任务状态。
 * 入参：page/appUrl/packId/routeName：页面、入口、临时 pack 和路线名。
 * 出参：Promise<string>，新建 session_id。
 * 异常：会话绑定、起点场景或任务标题不符时抛出 Error。
 */
async function createRouteSession(page, appUrl, packId, routeName) {
  const sessionId = await createSessionInApp(page, appUrl, packId);
  const initialDetail = await requestJson(page, `/api/sessions/${sessionId}`);
  const initialProgress = readSessionProgress(initialDetail.body);
  assertPlaytest(initialDetail.body.pack_id === packId, `${routeName} 新会话未绑定 A3 pack`);
  assertPlaytest(initialProgress.location === "market_square", `${routeName} 起点场景错误`);
  assertPlaytest(initialProgress.questStage === "gather_leads", `${routeName} 起始任务阶段错误`);
  assertPlaytest(
    (await page.locator("body").innerText()).includes("追查私盐契"),
    `${routeName} 页面未显示 A3 任务标题`
  );
  return sessionId;
}

/**
 * 功能：执行一条路线的所有玩家行动，并收集逐回合报告。
 * 入参：page/sessionId/routeName：页面、会话和路线名。
 * 出参：Promise<object>，包含 steps 与 lastTurn。
 * 异常：任一回合或断言失败时抛出 Error。
 */
async function runRouteSteps(page, sessionId, routeName) {
  const steps = [];
  let lastTurn = null;
  for (const [index, step] of routes[routeName].entries()) {
    const result = await submitAction(page, sessionId, step.action, index + 1);
    lastTurn = result.turn;
    const reportStep = assertStep(routeName, step, result, index + 1);
    if (step.expectConsequence) {
      const bodyText = await page.locator("body").innerText();
      assertPlaytest(bodyText.includes("选择后果"), `${routeName} 页面未展示选择后果`);
      assertPlaytest(bodyText.includes(step.branchPath), `${routeName} 页面未展示分支路线`);
    }
    steps.push(reportStep);
  }
  return { steps, lastTurn };
}

/**
 * 功能：断言路线最终完成、结案标记、分支后果和 SSE done 计数。
 * 入参：routeName（string）：路线名；steps（object[]）：逐回合报告。
 * 出参：object，最终步骤。
 * 异常：终局状态不符时抛出 Error。
 */
function assertRouteFinalState(routeName, steps) {
  const finalStep = steps[steps.length - 1];
  assertPlaytest(finalStep.questStatus === "completed", `${routeName} 最终任务未完成`);
  assertPlaytest(finalStep.questStage === "case_closed", `${routeName} 未到结案阶段`);
  assertPlaytest(
    finalStep.flags.includes("salt_contract_case_closed"),
    `${routeName} 缺少结案状态标记`
  );
  assertPlaytest(
    steps.some((step) => step.branch_consequence_count > 0),
    `${routeName} 没有任何结构化分支后果`
  );
  assertPlaytest(
    steps.every((step, idx) => step.sse_done_count >= idx + 1),
    `${routeName} 存在未消费到 done 的 SSE 回合`
  );
  return finalStep;
}

/**
 * 功能：刷新页面后重新加载会话，验证任务完成态可恢复。
 * 入参：page/sessionId/routeName：页面、会话和路线名。
 * 出参：Promise<void>。
 * 异常：恢复后任务状态丢失时抛出 Error。
 */
async function assertRouteRestore(page, sessionId, routeName) {
  await page.reload({ waitUntil: "networkidle", timeout: 30_000 });
  await page.getByLabel("输入或粘贴会话 ID").fill(sessionId);
  await page.getByRole("button", { name: /加载 \/ 保存/ }).click();
  await page.waitForFunction(
    (expectedText) => document.body.innerText.includes(expectedText),
    "私盐契结案",
    { timeout: 30_000 }
  );
  const restored = await requestJson(page, `/api/sessions/${sessionId}`);
  const progress = readSessionProgress(restored.body);
  assertPlaytest(
    progress.questStage === "case_closed" && progress.questStatus === "completed",
    `${routeName} 页面恢复后任务状态丢失`
  );
}

/**
 * 功能：截图、检查最终 trace，并断言页面没有 error 级异常。
 * 入参：page/routeName/sessionId/lastTurn/consoleRecords/pageErrors：路线运行证据。
 * 出参：Promise<object>，包含 screenshotPath 与 traceStages。
 * 异常：trace、页面异常或控制台 error 不符时抛出 Error。
 */
async function captureRouteEvidence(page, routeName, sessionId, lastTurn, consoleRecords, pageErrors) {
  const screenshotPath = resolve(REPORT_DIR, `a3-${routeName}-${sessionId}.png`);
  await page.screenshot({ path: screenshotPath, fullPage: true });
  const traceStages = readTraceStages(lastTurn ?? {});
  for (const expectedStage of EXPECTED_TRACE_STAGES) {
    assertPlaytest(
      traceStages.includes(expectedStage),
      `${routeName} 最后回合 trace 缺少阶段: ${expectedStage}`
    );
  }
  assertPlaytest(pageErrors.length === 0, `${routeName} 页面异常: ${JSON.stringify(pageErrors)}`);
  assertPlaytest(
    consoleRecords.filter((item) => item.type === "error").length === 0,
    `${routeName} 控制台 error: ${JSON.stringify(consoleRecords)}`
  );
  return { screenshotPath, traceStages };
}

/**
 * 功能：执行一条 A3 分支路线的真实 /app 试玩。
 * 入参：browser（Browser）：Playwright 浏览器；appUrl/packId/routeName：入口、包和路线名。
 * 出参：Promise<object>，包含会话、逐回合证据、截图和恢复检查。
 * 异常：任一 UI、回合、状态或恢复断言失败时向上抛出。
 */
async function runRoute(browser, appUrl, packId, routeName) {
  const { page, consoleRecords, pageErrors } = await createRoutePage(browser);
  try {
    const sessionId = await createRouteSession(page, appUrl, packId, routeName);
    const { steps, lastTurn } = await runRouteSteps(page, sessionId, routeName);
    const finalStep = assertRouteFinalState(routeName, steps);
    await assertRouteRestore(page, sessionId, routeName);
    const evidence = await captureRouteEvidence(
      page,
      routeName,
      sessionId,
      lastTurn,
      consoleRecords,
      pageErrors
    );
    return {
      route: routeName,
      session_id: sessionId,
      turn_count: steps.length,
      branch_path: finalStep.branchPath,
      completed: true,
      restored: true,
      screenshot_path: evidence.screenshotPath,
      trace_stages: evidence.traceStages,
      console_records: consoleRecords,
      page_errors: pageErrors,
      steps,
    };
  } finally {
    await page.close();
  }
}

/**
 * 功能：执行 A3 外部包导入与双分支 /app 端到端试玩。
 * 入参：无；可通过 LLMTRE_APP_URL/LLMTRE_API_URL 覆盖入口。
 * 出参：Promise<object>，包含导入、两条分支、截图、删除和报告路径。
 * 异常：导入、页面试玩、状态断言或清理失败时向上抛出。
 */
async function runPlaytest() {
  const appUrl = process.env.LLMTRE_APP_URL || DEFAULT_APP_URL;
  const packId = makeId("a3_external_branching");
  const importPayload = await buildImportPayload(packId);
  const report = {
    ok: false,
    pack_id: packId,
    imported: false,
    pack_deleted: false,
    routes: [],
    report_path: "",
  };

  let browser = null;
  try {
    await mkdir(REPORT_DIR, { recursive: true });
    const importResult = await requestApiJson("/api/story-packs", {
      method: "POST",
      body: importPayload,
    });
    assertPlaytest(importResult.status === 201, `A3 外部包导入失败: ${JSON.stringify(importResult)}`);
    assertPlaytest(importResult.body.summary?.pack_id === packId, "A3 导入返回 pack_id 错误");
    report.imported = true;

    const listAfterImport = await requestApiJson("/api/story-packs");
    const listedPackIds = (listAfterImport.body.packs ?? []).map((item) => item.pack_id);
    assertPlaytest(listedPackIds.includes(packId), "A3 导入后 pack 列表缺少临时包");

    browser = await chromium.launch({ headless: true });
    report.routes.push(await runRoute(browser, appUrl, packId, "public"));
    report.routes.push(await runRoute(browser, appUrl, packId, "private"));

    const routeCounts = report.routes.map((route) => route.turn_count);
    assertPlaytest(routeCounts.every((count) => count >= 5 && count <= 8), `A3 回合数不在 5-8: ${routeCounts}`);
    assertPlaytest(
      new Set(report.routes.map((route) => route.branch_path)).size === 2,
      "A3 两条试玩路线没有形成互斥分支"
    );

    const deleteResult = await requestApiJson(`/api/story-packs/${packId}`, { method: "DELETE" });
    assertPlaytest(deleteResult.status === 200, `A3 临时 pack 删除失败: ${JSON.stringify(deleteResult)}`);
    report.pack_deleted = true;
    report.ok = true;
    report.report_path = resolve(REPORT_DIR, `a3-branching-playtest-${packId}.json`);
    await writeFile(report.report_path, JSON.stringify(report, null, 2), "utf-8");
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
    console.log("A3_BRANCHING_UI_PLAYTEST_OK");
    console.log(JSON.stringify(report, null, 2));
  })
  .catch((error) => {
    console.error("A3_BRANCHING_UI_PLAYTEST_FAILED");
    console.error(error instanceof Error ? error.stack : String(error));
    process.exitCode = 1;
  });
