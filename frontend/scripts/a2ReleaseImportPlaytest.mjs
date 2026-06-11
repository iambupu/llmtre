import { mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import { dirname, extname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const DEFAULT_APP_URL = "http://localhost:5173/app/";
const DEFAULT_API_URL = "http://localhost:5000";
const SOURCE_PACK_ID = "echoes_under_red_lantern";
const QUEST_ID = "recover_the_tide_oath";
const SOURCE_PACK_DIR = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
  "story_packs",
  SOURCE_PACK_ID
);
const REPORT_DIR = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
  "test_runs",
  "a2-release"
);
const MIME_BY_EXTENSION = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".gif": "image/gif",
  ".mp4": "video/mp4",
  ".webm": "video/webm",
  ".ogv": "video/ogg",
  ".mov": "video/quicktime",
  ".mp3": "audio/mpeg",
  ".wav": "audio/wav",
  ".ogg": "audio/ogg",
  ".m4a": "audio/mp4",
  ".flac": "audio/flac",
};

const route = [
  {
    action: "询问船夫任伯",
    location: "ferry_landing",
    questStage: "read_the_notice",
    triggerId: "talk_boatman_ren",
    flag: "boatman_heard_wrong_bell",
  },
  {
    action: "检查潮汐告示",
    location: "ferry_landing",
    questStage: "compare_witnesses",
    triggerId: "inspect_tide_notice",
    flag: "notice_scraped_name_found",
  },
  {
    action: "前往旧账房",
    location: "ledgers_room",
    questStage: "compare_witnesses",
    triggerId: null,
  },
  {
    action: "询问账房燕书吏",
    location: "ledgers_room",
    questStage: "compare_witnesses",
    triggerId: "talk_scribe_yan",
    flag: "scribe_yan_missing_page",
  },
  {
    action: "检查潮税账册",
    location: "ledgers_room",
    questStage: "unseal_the_bell",
    triggerId: "inspect_tide_ledger",
    flag: "ledger_second_boat_found",
  },
  {
    action: "从后门回赤灯巷",
    location: "red_lantern_lane",
    questStage: "unseal_the_bell",
    triggerId: null,
  },
  {
    action: "询问守灯人莫婶",
    location: "red_lantern_lane",
    questStage: "unseal_the_bell",
    triggerId: "talk_lantern_keeper_mo",
    flag: "lantern_keeper_knows_oath",
  },
  {
    action: "检查反复打结的灯绳",
    location: "red_lantern_lane",
    questStage: "unseal_the_bell",
    triggerId: "inspect_lantern_knots",
    flag: "three_knot_order_seen",
  },
  {
    action: "走向静默钟院",
    location: "bell_courtyard",
    questStage: "unseal_the_bell",
    triggerId: null,
  },
  {
    action: "询问石匠阿砺",
    location: "bell_courtyard",
    questStage: "unseal_the_bell",
    triggerId: "talk_stone_mender_li",
    flag: "stone_mender_revealed_stairs",
  },
  {
    action: "检查静默潮钟",
    location: "bell_courtyard",
    questStage: "recover_oath",
    triggerId: "inspect_silent_bell",
    flag: "silent_bell_unsealed",
  },
  {
    action: "沿钟后石阶进入潮下水窖",
    location: "tide_cellar",
    questStage: "recover_oath",
    triggerId: null,
  },
  {
    action: "检查盐壳封住的旧锁",
    location: "tide_cellar",
    questStage: "recover_oath",
    triggerId: "inspect_salt_lock",
    flag: "salt_lock_maintained",
  },
  {
    action: "检查不映人脸的水镜",
    location: "tide_cellar",
    questStage: "return_to_dawn",
    triggerId: "inspect_water_mirror",
    flag: "tide_oath_shard_recovered",
  },
  {
    action: "沿退潮露出的石梁走向晓桥",
    location: "dawn_causeway",
    questStage: "return_to_dawn",
    triggerId: "enter_dawn_causeway_complete",
    questStatus: "completed",
    flag: "red_lantern_story_complete",
  },
  {
    action: "观察清晨的桥面",
    location: "dawn_causeway",
    questStage: "return_to_dawn",
    triggerId: "inspect_dawn_bridge",
    questStatus: "completed",
    flag: "dawn_bridge_seen",
  },
];

/**
 * 功能：断言 Release 验收条件并输出稳定错误信息。
 * 入参：condition（boolean）：断言结果；message（string）：失败说明。
 * 出参：void。
 * 异常：condition 为 false 时抛出 Error，中断本次 Release 试玩。
 */
function assertPlaytest(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

/**
 * 功能：生成只包含小写字母、数字和下划线的临时 pack_id，满足后端 ID 约束。
 * 入参：prefix（string）：业务前缀。
 * 出参：string，稳定可清理的临时 pack_id。
 * 异常：无。
 */
function makePackId(prefix) {
  return `${prefix}_${Date.now().toString(36)}_${Math.random()
    .toString(36)
    .slice(2, 8)}`;
}

/**
 * 功能：生成后端幂等接口可接受的 request_id。
 * 入参：scope（string）：请求场景前缀。
 * 出参：string，长度与字符集满足后端校验。
 * 异常：无。
 */
function makeRequestId(scope) {
  return `req_${scope}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

/**
 * 功能：把 API 相对路径解析为 Flask 直连 URL，避开 Vite dev proxy 长回合超时噪声。
 * 入参：path（string）：以 /api 开头的路径。
 * 出参：string，绝对 URL。
 * 异常：URL 构造失败时由 URL 抛出 TypeError。
 */
function resolveApiUrl(path) {
  const apiBaseUrl = process.env.LLMTRE_API_URL || DEFAULT_API_URL;
  return new URL(path, apiBaseUrl).toString();
}

/**
 * 功能：通过 Node fetch 直连 Flask JSON API，保留统一响应 envelope。
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
  const body = text ? JSON.parse(text) : {};
  return { status: response.status, body };
}

/**
 * 功能：读取 UTF-8 JSON 文件并解析为对象。
 * 入参：path（string）：JSON 文件绝对路径或相对路径。
 * 出参：Promise<object>，解析后的 JSON。
 * 异常：文件读取或 JSON 解析失败时向上抛出。
 */
async function readJson(path) {
  return JSON.parse(await readFile(path, "utf-8"));
}

/**
 * 功能：读取 Story Pack JSON 集合目录，按对象 ID 组成导入 payload 字段。
 * 入参：dir（string）：集合目录；idField（string）：对象内稳定 ID 字段。
 * 出参：Promise<object>，键为对象 ID，值为 JSON 对象。
 * 异常：目录读取、JSON 解析或 ID 缺失时抛出 Error。
 */
async function readJsonCollection(dir, idField) {
  const entries = await readdir(dir, { withFileTypes: true });
  const items = {};
  for (const entry of entries) {
    if (!entry.isFile() || !entry.name.endsWith(".json")) {
      continue;
    }
    const item = await readJson(join(dir, entry.name));
    const itemId = String(item[idField] ?? "").trim();
    assertPlaytest(itemId.length > 0, `${relative(process.cwd(), join(dir, entry.name))} 缺少 ${idField}`);
    items[itemId] = item;
  }
  return items;
}

/**
 * 功能：读取 Story Pack lore 文本集合，保持文件名到文本内容的导入形状。
 * 入参：dir（string）：lore 目录。
 * 出参：Promise<object>，键为文件名，值为 Markdown 文本。
 * 异常：目录读取或文件读取失败时向上抛出。
 */
async function readLoreCollection(dir) {
  const entries = await readdir(dir, { withFileTypes: true });
  const items = {};
  for (const entry of entries) {
    if (entry.isFile()) {
      items[entry.name] = await readFile(join(dir, entry.name), "utf-8");
    }
  }
  return items;
}

/**
 * 功能：把源 pack assets 下的媒体文件转成导入 API 接受的 data URL。
 * 入参：manifest（object）：源 manifest；packDir（string）：源 pack 根目录。
 * 出参：Promise<object>，键为 assets/ 下相对路径，值为 data URL。
 * 异常：资源扩展名不支持或文件读取失败时抛出 Error。
 */
async function buildAssetFiles(manifest, packDir) {
  const assetFiles = {};
  for (const asset of Object.values(manifest.assets ?? {})) {
    const assetSrc = String(asset?.src ?? "").trim();
    if (!assetSrc) {
      continue;
    }
    const suffix = extname(assetSrc).toLowerCase();
    const mimeType = MIME_BY_EXTENSION[suffix];
    assertPlaytest(Boolean(mimeType), `Release 导入脚本缺少 MIME 映射: ${assetSrc}`);
    const content = await readFile(join(packDir, "assets", assetSrc));
    assetFiles[assetSrc] = `data:${mimeType};base64,${content.toString("base64")}`;
  }
  return assetFiles;
}

/**
 * 功能：把现有赤灯包转换为外部上传 payload，并替换 pack_id 以避免污染源包。
 * 入参：packId（string）：临时外部 pack ID。
 * 出参：Promise<object>，可直接 POST 到 /api/story-packs。
 * 异常：源 pack 文件缺失、格式错误或资源读取失败时向上抛出。
 */
async function buildImportPayload(packId) {
  const manifest = await readJson(join(SOURCE_PACK_DIR, "manifest.json"));
  manifest.pack_id = packId;
  manifest.title = `${manifest.title}（Release 导入验收）`;
  manifest.author = "TRE Release Playtest";
  return {
    manifest,
    scenes: await readJsonCollection(join(SOURCE_PACK_DIR, "scenes"), "scene_id"),
    quests: await readJsonCollection(join(SOURCE_PACK_DIR, "quests"), "quest_id"),
    triggers: await readJsonCollection(join(SOURCE_PACK_DIR, "triggers"), "trigger_id"),
    lore: await readLoreCollection(join(SOURCE_PACK_DIR, "lore")),
    asset_files: await buildAssetFiles(manifest, SOURCE_PACK_DIR),
  };
}

/**
 * 功能：通过浏览器同源上下文请求 JSON API，覆盖 GET/POST/DELETE 验收操作。
 * 入参：page（Page）：Playwright 页面；path（string）：API 路径；options（object）：method/body。
 * 出参：Promise<{status:number, body:object}>，保留统一响应 envelope。
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
      const body = text ? JSON.parse(text) : {};
      return { status: response.status, body };
    },
    { urlPath: path, requestOptions: options }
  );
}

/**
 * 功能：在页面内安装 SSE 记录器，复制读取 /turns/stream 响应以保留 done/error 事件证据。
 * 入参：page（Page）：Playwright 页面。
 * 出参：Promise<void>。
 * 异常：addInitScript 注入失败时向上抛出；运行期解析失败只写入记录，不阻断页面原始 fetch。
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
 * 功能：读取页面内已记录的 SSE 事件，用于验证流式回合调试证据。
 * 入参：page（Page）：Playwright 页面。
 * 出参：Promise<object[]>，按捕获顺序排列的 SSE 事件。
 * 异常：page.evaluate 失败时向上抛出。
 */
async function readRecordedSseEvents(page) {
  return await page.evaluate(() => window.__treSseEvents ?? []);
}

/**
 * 功能：从会话详情响应提取当前位置、任务阶段、任务状态和角色标记。
 * 入参：sessionBody（object）：GET /api/sessions/{session_id} 的响应主体。
 * 出参：object，包含 location、flags、questStatus、questStage。
 * 异常：不主动抛出；缺失字段按空值降级，后续断言负责判定。
 */
function readSessionProgress(sessionBody) {
  const activeCharacter = sessionBody.active_character ?? {};
  const quests = sessionBody.scene_snapshot?.active_quests ?? [];
  const quest = Array.isArray(quests)
    ? quests.find((item) => item.quest_id === QUEST_ID)
    : null;
  return {
    location: activeCharacter.location ?? "",
    flags: Array.isArray(activeCharacter.state_flags) ? activeCharacter.state_flags : [],
    questStatus: quest?.status ?? "",
    questStage: quest?.current_stage_id ?? "",
  };
}

/**
 * 功能：通过 /app 选择刚导入的外部 pack 并创建会话。
 * 入参：page（Page）：Playwright 页面；appUrl（string）：/app URL；packId（string）：临时 pack ID。
 * 出参：Promise<string>，新建 session_id。
 * 异常：页面加载、pack 选项刷新或会话创建超时时向上抛出。
 */
async function createImportedPackSession(page, appUrl, packId) {
  await page.goto(appUrl, { waitUntil: "networkidle", timeout: 30_000 });
  await page.waitForFunction(
    (expectedPackId) =>
      [...document.querySelectorAll("select option")].some(
        (item) => item.value === expectedPackId
      ),
    packId,
    { timeout: 20_000 }
  );
  const sessionInput = page.locator('input[value^="sess_"]').first();
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
 * 功能：用玩家输入框提交一条行动，并等待回合历史落库。
 * 入参：page（Page）：Playwright 页面；sessionId（string）：当前会话；
 *   actionText（string）：玩家输入；expectedTotal（number）：期望历史总回合数。
 * 出参：Promise<object>，包含回合详情与提交后的会话详情。
 * 异常：发送按钮不可用、SSE 未完成或历史未落库时抛出 Error。
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
        { timeout: 5_000 }
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
        persisted_turn: turnDetail.body,
        session: sessionDetail.body,
        sse_event_count: sseEvents.length,
        sse_done_count: donePayloads.length,
      };
    }
    await page.waitForTimeout(1_000);
  }
  throw new Error(`等待回合 ${expectedTotal} 完成超时: ${JSON.stringify(lastHistory)}`);
}

/**
 * 功能：统计试玩路线中经历的场景、可见交互与触发器数量。
 * 入参：steps（object[]）：试玩步骤报告。
 * 出参：object，包含 scene_count、interaction_trigger_count、turn_count。
 * 异常：无。
 */
function summarizeCoverage(steps) {
  const scenes = new Set();
  const interactionTriggers = new Set();
  for (const step of steps) {
    if (step.location) {
      scenes.add(step.location);
    }
    for (const triggerId of step.trigger_ids ?? []) {
      if (!String(triggerId).startsWith("enter_")) {
        interactionTriggers.add(triggerId);
      }
    }
  }
  return {
    scene_count: scenes.size,
    interaction_trigger_count: interactionTriggers.size,
    turn_count: steps.filter((step) => step.action !== "创建会话").length,
  };
}

/**
 * 功能：提取最后一个回合的 trace 阶段，作为调试能力未丢失的验收证据。
 * 入参：turnBody（object）：GET turn detail 响应。
 * 出参：string[]，trace.stages 中的阶段名。
 * 异常：无；缺失 trace 时返回空数组，由调用方断言。
 */
function readTraceStages(turnBody) {
  const stages = turnBody.trace?.stages ?? [];
  return Array.isArray(stages) ? stages.map((item) => item.stage).filter(Boolean) : [];
}

/**
 * 功能：从 SSE 文本缓冲中解析完整事件块。
 * 入参：buffer（string）：上一轮残留文本；chunkText（string）：本轮新增文本。
 * 出参：object，包含 events 与 remaining。
 * 异常：不抛出 JSON 解析异常；解析失败会把原文记录到 payload。
 */
function parseSseChunk(buffer, chunkText) {
  const combined = `${buffer}${chunkText}`;
  const parts = combined.split(/\n\n+/);
  const remaining = combined.endsWith("\n\n") ? "" : parts.pop() ?? "";
  const events = [];
  for (const block of parts) {
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
    events.push({ event, payload });
  }
  return { events, remaining };
}

/**
 * 功能：通过 Flask SSE 接口提交回合，并返回 done payload 与完整事件记录。
 * 入参：sessionId（string）：会话 ID；actionText（string）：玩家行动文本。
 * 出参：Promise<object>，包含 turn、events、done_count。
 * 异常：HTTP 非 2xx、SSE error 事件或流结束无 done 时抛出 Error。
 */
async function submitActionStream(sessionId, actionText) {
  const response = await fetch(resolveApiUrl(`/api/sessions/${sessionId}/turns/stream`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      request_id: makeRequestId("release_turn"),
      character_id: "player_01",
      sandbox_mode: false,
      user_input: actionText,
    }),
  });
  if (!response.ok || !response.body) {
    throw new Error(`SSE 回合请求失败: ${response.status}`);
  }

  const decoder = new TextDecoder("utf-8");
  const reader = response.body.getReader();
  let buffer = "";
  let donePayload = null;
  const events = [];
  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    const parsed = parseSseChunk(buffer, decoder.decode(value, { stream: true }));
    buffer = parsed.remaining;
    for (const event of parsed.events) {
      events.push(event);
      if (event.event === "error") {
        throw new Error(`SSE error: ${JSON.stringify(event.payload)}`);
      }
      if (event.event === "done") {
        donePayload = event.payload;
      }
    }
  }
  if (!donePayload) {
    throw new Error("SSE 流结束但没有 done 事件");
  }
  return {
    turn: donePayload,
    events,
    done_count: events.filter((event) => event.event === "done").length,
  };
}

/**
 * 功能：提交一条真实流式回合，并读取会话与历史确认该回合已持久化。
 * 入参：sessionId（string）：会话 ID；actionText（string）：玩家行动文本；
 *   expectedTotal（number）：期望历史回合数。
 * 出参：Promise<object>，包含 SSE turn、会话详情与 done 计数。
 * 异常：SSE 失败、历史未达预期或会话读取失败时抛出 Error。
 */
async function submitActionViaApi(sessionId, actionText, expectedTotal) {
  const streamed = await submitActionStream(sessionId, actionText);
  const history = await requestApiJson(
    `/api/sessions/${sessionId}/turns?page=1&page_size=100`
  );
  assertPlaytest(
    history.status === 200 && history.body.total >= expectedTotal,
    `回合历史未落库: expected=${expectedTotal}, actual=${history.body.total}`
  );
  const session = await requestApiJson(`/api/sessions/${sessionId}`);
  assertPlaytest(session.status === 200, `会话读取失败: ${JSON.stringify(session)}`);
  return {
    turn: streamed.turn,
    session: session.body,
    sse_done_count: streamed.done_count,
    sse_event_count: streamed.events.length,
  };
}

/**
 * 功能：执行 A2-Release 外部导入包完整试玩，并写出截图和 JSON 验收记录。
 * 入参：无；可通过 LLMTRE_APP_URL 覆盖 /app 地址。
 * 出参：Promise<object>，包含导入、试玩、删包、历史保留和调试证据。
 * 异常：任一导入、UI 行动、任务推进或清理断言失败时向上抛出。
 */
async function runPlaytest() {
  const appUrl = process.env.LLMTRE_APP_URL || DEFAULT_APP_URL;
  const packId = makePackId("release_external_red_lantern");
  const badPackId = makePackId("release_external_bad");
  const importPayload = await buildImportPayload(packId);
  const badPayload = await buildImportPayload(badPackId);
  badPayload.manifest.start_scene_id = "missing_release_scene";

  const consoleRecords = [];
  const pageErrors = [];
  let browser = null;
  let page = null;

  const report = {
    ok: false,
    pack_id: packId,
    bad_pack_id: badPackId,
    session_id: "",
    imported: false,
    bad_pack_rejected: false,
    steps: [],
    coverage: {},
    session_retained_after_pack_delete: false,
    history_retained_after_pack_delete: false,
    pack_deleted: false,
    screenshot_path: "",
    report_path: "",
    console_records: consoleRecords,
    page_errors: pageErrors,
    trace_stages: [],
  };

  try {
    const badImport = await requestApiJson("/api/story-packs", {
      method: "POST",
      body: badPayload,
    });
    assertPlaytest(badImport.status === 400, `坏包导入应返回 400，实际 ${badImport.status}`);
    assertPlaytest(
      badImport.body.error?.code === "PACK_IMPORT_FAILED",
      `坏包导入错误码不正确: ${JSON.stringify(badImport.body.error)}`
    );
    report.bad_pack_rejected = true;

    const importResult = await requestApiJson("/api/story-packs", {
      method: "POST",
      body: importPayload,
    });
    assertPlaytest(importResult.status === 201, `外部包导入失败: ${JSON.stringify(importResult)}`);
    assertPlaytest(
      importResult.body.summary?.pack_id === packId,
      `导入返回 pack_id 错误: ${JSON.stringify(importResult.body.summary)}`
    );
    report.imported = true;

    const listAfterImport = await requestApiJson("/api/story-packs");
    const listedPackIds = (listAfterImport.body.packs ?? []).map((item) => item.pack_id);
    assertPlaytest(listedPackIds.includes(packId), "导入后 pack 列表缺少外部包");
    assertPlaytest(!listedPackIds.includes(badPackId), "坏包不应进入可创建 pack 列表");

    browser = await chromium.launch({ headless: true });
    page = await browser.newPage({ viewport: { width: 1280, height: 920 } });
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
    await page.goto(appUrl, { waitUntil: "networkidle", timeout: 30_000 });
    await page.waitForFunction(
      (expectedPackId) =>
        [...document.querySelectorAll("select option")].some(
          (item) => item.value === expectedPackId
        ),
      packId,
      { timeout: 20_000 }
    );
    await page.locator("select").first().selectOption({ value: packId });
    await mkdir(REPORT_DIR, { recursive: true });
    report.screenshot_path = resolve(REPORT_DIR, `release-import-playtest-${packId}.png`);
    await page.screenshot({ path: report.screenshot_path, fullPage: true });
    await browser.close();
    browser = null;
    page = null;

    const createdSession = await requestApiJson("/api/sessions", {
      method: "POST",
      body: {
        request_id: makeRequestId("release_create"),
        character_id: "player_01",
        sandbox_mode: false,
        pack_id: packId,
      },
    });
    assertPlaytest(
      createdSession.status === 201,
      `导入包创建会话失败: ${JSON.stringify(createdSession)}`
    );
    report.session_id = createdSession.body.session_id;
    const initialDetail = await requestApiJson(`/api/sessions/${report.session_id}`);
    assertPlaytest(initialDetail.body.pack_id === packId, "新会话未绑定导入包");
    const initialProgress = readSessionProgress(initialDetail.body);
    assertPlaytest(
      initialProgress.flags.includes("red_lantern_case_started"),
      "创建会话后缺少 red_lantern_case_started 初始标记"
    );
    assertPlaytest(
      initialProgress.questStatus === "active" &&
        initialProgress.questStage === "read_the_notice",
      `创建会话后任务初始状态错误: ${JSON.stringify(initialProgress)}`
    );
    report.steps.push({ action: "创建会话", ...initialProgress });

    let lastTurn = null;
    for (const [index, step] of route.entries()) {
      const result = await submitActionViaApi(report.session_id, step.action, index + 1);
      lastTurn = result.turn;
      const progress = readSessionProgress(result.session);
      const triggerIds = (result.turn.trigger_events ?? []).map((event) => event.trigger_id);
      assertPlaytest(
        progress.location === step.location,
        `${step.action} 后位置错误: expected=${step.location}, actual=${progress.location}`
      );
      assertPlaytest(
        progress.questStage === step.questStage,
        `${step.action} 后任务阶段错误: expected=${step.questStage}, actual=${progress.questStage}`
      );
      if (step.questStatus) {
        assertPlaytest(
          progress.questStatus === step.questStatus,
          `${step.action} 后任务状态错误: expected=${step.questStatus}, actual=${progress.questStatus}`
        );
      }
      if (step.triggerId) {
        assertPlaytest(
          triggerIds.includes(step.triggerId),
          `${step.action} 后缺少触发器: ${step.triggerId}`
        );
      }
      if (step.flag) {
        assertPlaytest(
          progress.flags.includes(step.flag),
          `${step.action} 后缺少状态标记: ${step.flag}`
        );
      }
      report.steps.push({
        action: step.action,
        trigger_ids: triggerIds,
        sse_done_count: result.sse_done_count,
        final_response: String(result.turn.final_response ?? "").slice(0, 180),
        ...progress,
      });
    }

    const finalStep = report.steps[report.steps.length - 1];
    report.coverage = summarizeCoverage(report.steps);
    assertPlaytest(report.coverage.turn_count >= 10, "外部导入包试玩未达到 10 回合");
    assertPlaytest(report.coverage.scene_count >= 3, "外部导入包试玩未达到 3 个场景");
    assertPlaytest(
      report.coverage.interaction_trigger_count >= 3,
      "外部导入包试玩未达到 3 个可见交互"
    );
    assertPlaytest(finalStep.location === "dawn_causeway", "主线未停在晓桥结局场景");
    assertPlaytest(finalStep.questStatus === "completed", "主线任务未完成");
    assertPlaytest(
      finalStep.flags.includes("red_lantern_story_complete"),
      "结局缺少 red_lantern_story_complete 标记"
    );

    const historyBeforeDelete = await requestApiJson(
      `/api/sessions/${report.session_id}/turns?page=1&page_size=100`
    );
    assertPlaytest(historyBeforeDelete.body.total >= 16, "删包前回合历史未完整落库");
    const sessionBeforeDelete = await requestApiJson(`/api/sessions/${report.session_id}`);
    const frozenPackHash = sessionBeforeDelete.body.compiled_artifact_hash;
    assertPlaytest(Boolean(frozenPackHash), "会话缺少冻结 compiled_artifact_hash");

    report.trace_stages = readTraceStages(lastTurn ?? {});
    for (const expectedStage of ["api.received", "state.updated", "gm.rendered", "outer.emitted"]) {
      assertPlaytest(
        report.trace_stages.includes(expectedStage),
        `最后回合 trace 缺少阶段: ${expectedStage}`
      );
    }

    const deletePack = await requestApiJson(`/api/story-packs/${packId}`, {
      method: "DELETE",
    });
    assertPlaytest(deletePack.status === 200, `删除导入包失败: ${JSON.stringify(deletePack)}`);
    report.pack_deleted = true;
    const listAfterDelete = await requestApiJson("/api/story-packs");
    const remainingPackIds = (listAfterDelete.body.packs ?? []).map((item) => item.pack_id);
    assertPlaytest(!remainingPackIds.includes(packId), "删除后 pack 仍出现在列表中");

    const sessionAfterDelete = await requestApiJson(`/api/sessions/${report.session_id}`);
    assertPlaytest(sessionAfterDelete.status === 200, "删除 pack 后历史 session 不应消失");
    assertPlaytest(sessionAfterDelete.body.pack_id === packId, "删除 pack 后旧 session pack_id 被改写");
    assertPlaytest(
      sessionAfterDelete.body.compiled_artifact_hash === frozenPackHash,
      "删除 pack 后旧 session 冻结 hash 被改写"
    );
    report.session_retained_after_pack_delete = true;

    const historyAfterDelete = await requestApiJson(
      `/api/sessions/${report.session_id}/turns?page=1&page_size=100`
    );
    assertPlaytest(
      historyAfterDelete.status === 200 && historyAfterDelete.body.total >= 16,
      "删除 pack 后历史回合未保留"
    );
    report.history_retained_after_pack_delete = true;
    assertPlaytest(pageErrors.length === 0, `页面运行异常: ${JSON.stringify(pageErrors)}`);
    assertPlaytest(
      consoleRecords.filter((item) => item.type === "error").length === 0,
      `控制台存在 error: ${JSON.stringify(consoleRecords)}`
    );

    report.ok = true;
    report.report_path = resolve(REPORT_DIR, `release-import-playtest-${packId}.json`);
    await writeFile(report.report_path, JSON.stringify(report, null, 2), "utf-8");
    return report;
  } finally {
    if (report.imported && !report.pack_deleted) {
      await requestApiJson(`/api/story-packs/${packId}`, { method: "DELETE" }).catch(() => {});
    }
    if (browser) {
      await browser.close();
    }
  }
}

runPlaytest()
  .then((report) => {
    console.log("A2_RELEASE_IMPORT_PLAYTEST_OK");
    console.log(JSON.stringify(report, null, 2));
  })
  .catch((error) => {
    console.error("A2_RELEASE_IMPORT_PLAYTEST_FAILED");
    console.error(error instanceof Error ? error.stack : String(error));
    process.exitCode = 1;
  });
