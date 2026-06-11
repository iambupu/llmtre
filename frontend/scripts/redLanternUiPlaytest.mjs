import { mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { chromium } from "playwright";

const DEFAULT_APP_URL = "http://localhost:5173/app/";
const PACK_ID = "echoes_under_red_lantern";
const QUEST_ID = "recover_the_tide_oath";

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
 * 功能：断言验收条件并输出稳定错误信息。
 * 入参：condition（boolean）：断言结果；message（string）：失败说明。
 * 出参：void。
 * 异常：condition 为 false 时抛出 Error，中断本次 UI 试玩。
 */
function assertPlaytest(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

/**
 * 功能：通过浏览器上下文请求 JSON API，并剥离 success envelope。
 * 入参：page（Page）：Playwright 页面；path（string）：以 / 开头的 API 路径。
 * 出参：Promise<{status:number, body:object}>，body 为 data 字段或原始响应。
 * 异常：fetch 或 JSON 解析失败时向上抛出。
 */
async function fetchJson(page, path) {
  return await page.evaluate(async (urlPath) => {
    const response = await fetch(urlPath);
    const body = await response.json();
    return { status: response.status, body: body.data ?? body };
  }, path);
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
 * 功能：在 /app 页面选择赤灯剧本并创建一个临时会话。
 * 入参：page（Page）：Playwright 页面；appUrl（string）：/app URL。
 * 出参：Promise<string>，新建 session_id。
 * 异常：页面加载、剧本选择或会话创建超时时向上抛出。
 */
async function createPackSession(page, appUrl) {
  await page.goto(appUrl, { waitUntil: "networkidle", timeout: 30_000 });
  const packSelect = page.locator("select").first();
  await packSelect.selectOption({ value: PACK_ID }).catch(async () => {
    await packSelect.selectOption({ index: 1 });
  });
  await page.waitForFunction(
    () => {
      const button = [...document.querySelectorAll("button")].find((item) =>
        item.innerText.includes("新会话")
      );
      return Boolean(button && !button.disabled);
    },
    null,
    { timeout: 10_000 }
  );
  await page.getByRole("button", { name: /新会话/ }).click();
  const sessionInput = page.locator('input[value^="sess_"]').first();
  await sessionInput.waitFor({ state: "visible", timeout: 20_000 });
  return await sessionInput.inputValue();
}

/**
 * 功能：用玩家输入框提交一条行动，并等待该回合持久化完成。
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
    lastHistory = await fetchJson(
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
      const turnDetail = await fetchJson(
        page,
        `/api/sessions/${sessionId}/turns/${latest.session_turn_id}`
      );
      const sessionDetail = await fetchJson(page, `/api/sessions/${sessionId}`);
      return { turn: turnDetail.body, session: sessionDetail.body };
    }
    await page.waitForTimeout(1_000);
  }
  throw new Error(`等待回合 ${expectedTotal} 完成超时: ${JSON.stringify(lastHistory)}`);
}

/**
 * 功能：执行赤灯主线与关键支线 UI 试玩并返回结构化报告。
 * 入参：无；通过 LLMTRE_APP_URL 环境变量可覆盖 /app 地址。
 * 出参：Promise<object>，包含 session_id、逐步断言结果、最终截图路径。
 * 异常：任一 UI 行动、任务推进或清理断言失败时向上抛出。
 */
async function runPlaytest() {
  const appUrl = process.env.LLMTRE_APP_URL || DEFAULT_APP_URL;
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 920 } });
  const report = {
    ok: false,
    session_id: "",
    steps: [],
    screenshot_path: "",
    deleted: false,
  };

  try {
    report.session_id = await createPackSession(page, appUrl);
    const initialDetail = await fetchJson(page, `/api/sessions/${report.session_id}`);
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

    for (const [index, step] of route.entries()) {
      const result = await submitAction(page, report.session_id, step.action, index + 1);
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
        final_response: String(result.turn.final_response ?? "").slice(0, 180),
        ...progress,
      });
    }

    const finalStep = report.steps[report.steps.length - 1];
    assertPlaytest(finalStep.location === "dawn_causeway", "主线未停在晓桥结局场景");
    assertPlaytest(finalStep.questStatus === "completed", "主线任务未完成");
    assertPlaytest(
      finalStep.flags.includes("red_lantern_story_complete"),
      "结局缺少 red_lantern_story_complete 标记"
    );
    report.screenshot_path = resolve(
      process.cwd(),
      "..",
      ".agent_context",
      `red-lantern-ui-playtest-${Date.now()}.png`
    );
    await mkdir(dirname(report.screenshot_path), { recursive: true });
    await page.screenshot({ path: report.screenshot_path, fullPage: true });
    report.ok = true;
    return report;
  } finally {
    if (report.session_id) {
      const cleanup = await page.evaluate(async (sessionId) => {
        const response = await fetch(`/api/sessions/${sessionId}`, { method: "DELETE" });
        return response.status;
      }, report.session_id);
      report.deleted = cleanup === 200;
    }
    await browser.close();
  }
}

runPlaytest()
  .then((report) => {
    console.log("RED_LANTERN_UI_PLAYTEST_OK");
    console.log(JSON.stringify(report, null, 2));
  })
  .catch((error) => {
    console.error("RED_LANTERN_UI_PLAYTEST_FAILED");
    console.error(error instanceof Error ? error.stack : String(error));
    process.exitCode = 1;
  });
