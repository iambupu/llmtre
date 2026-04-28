const state = {
    sessionId: "",
    page: 1,
    pageSize: 20,
    requestCounter: 0,
    logs: [],
    conversation: [],
    lastPayload: null,
    lastRequest: null,
    lastSseEvent: "idle",
    debugVisible: true,
    outputMode: localStorage.getItem("tre.outputMode") || "stream",
    streamingGmText: "",
    streamingGmIndex: null,
    lastNonEmptyTrace: [],
    isBusy: false,
};

/**
 * 功能：转义文本后再写入 HTML，避免 API 响应内容破坏页面结构。
 * 入参：value（any）：待展示值。
 * 出参：string，可安全拼入模板字符串的文本。
 * 异常：无显式异常；未知值统一转字符串。
 */
function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

/**
 * 功能：记录运行状态日志，帮助定位前端与 API 交互问题。
 * 入参：message（string）日志文本。
 * 出参：void。
 * 异常：DOM 不存在时仅更新内存日志，不抛异常。
 */
function logStatus(message) {
    const now = new Date().toISOString();
    state.logs.unshift(`[${now}] ${message}`);
    state.logs = state.logs.slice(0, 80);
    const statusLog = document.getElementById("statusLog");
    if (statusLog) {
        statusLog.textContent = state.logs.join("\n");
    }
}

/**
 * 功能：生成写请求幂等键，避免重复点击导致重复推进回合。
 * 入参：scope（string）请求作用域标识。
 * 出参：string，符合后端 request_id 格式要求。
 * 异常：时间函数失败时异常向上抛出。
 */
function nextRequestId(scope) {
    state.requestCounter += 1;
    const compact = Date.now().toString(36);
    const seq = state.requestCounter.toString(36).padStart(4, "0");
    return `${scope}_${compact}_${seq}`;
}

/**
 * 功能：执行同源 API 请求并标准化错误处理，同时记录请求与错误响应。
 * 入参：url（string）接口路径。options（RequestInit）fetch 参数。
 * 出参：Promise<object>，返回 JSON 结果对象。
 * 异常：网络异常或 API 返回 `ok=false` 时抛出 Error，并保留最近请求体。
 */
async function callApi(url, options = {}) {
    const method = options.method || "GET";
    state.lastRequest = {url, method, body: options.body || ""};
    logStatus(`API ${method} ${url}`);
    const response = await fetch(url, {
        headers: {"Content-Type": "application/json"},
        ...options,
    });
    let data = {};
    try {
        data = await response.json();
    } catch {
        data = {ok: false, error: {message: `HTTP ${response.status} 非 JSON 响应`}};
    }
    if (!response.ok || data.ok === false) {
        const message = data?.error?.message || `HTTP ${response.status}`;
        logStatus(`API 失败 ${method} ${url}: ${message}`);
        renderPlayerFacingError(message, {
            status: response.status,
            response: data,
        });
        throw new Error(message);
    }
    return data;
}

/**
 * 功能：将对象以格式化 JSON 渲染到目标区域。
 * 入参：elementId（string）DOM ID。payload（any）待渲染数据。
 * 出参：void。
 * 异常：DOM 不存在时静默返回；JSON 序列化异常向上抛出。
 */
function renderJson(elementId, payload) {
    const el = document.getElementById(elementId);
    if (!el) {
        return;
    }
    el.textContent = JSON.stringify(payload, null, 2);
}

/**
 * 功能：把后端失败转换为玩家可见的系统消息，同时保留调试区原始错误。
 * 入参：message（string）：面向玩家的错误文案；detail（object）：请求与响应细节。
 * 出参：void。
 * 异常：DOM 缺失时降级为状态日志，不阻断后续错误处理。
 */
function renderPlayerFacingError(message, detail = {}) {
    const text = textOr(message, "回合执行失败，请稍后重试。");
    renderJson("turnResult", {
        request: state.lastRequest,
        error: detail,
        player_message: text,
    });
    appendConversation("system", "系统", text);
    setStreamStatus(`失败：${text}`);
    logStatus(`玩家可见失败：${text}`);
}

/**
 * 功能：读取对象字段并保证返回可展示文本。
 * 入参：value（any）：字段值；fallback（string）：缺省文案。
 * 出参：string，展示文本。
 * 异常：无显式异常。
 */
function textOr(value, fallback) {
    const text = String(value ?? "").trim();
    return text || fallback;
}

/**
 * 功能：设置元素文本。
 * 入参：id（string）：DOM ID；text（string）：文本。
 * 出参：void。
 * 异常：DOM 缺失时静默返回。
 */
function setText(id, text) {
    const el = document.getElementById(id);
    if (el) {
        el.textContent = text;
    }
}

/**
 * 功能：切换页面忙碌态，防止 LLM 运算期间重复点击并给玩家明确反馈。
 * 入参：isBusy（boolean）：是否进入忙碌态；message（string，默认空）：状态栏展示文案。
 * 出参：void。
 * 异常：DOM 缺失时静默跳过对应控件；不会阻断当前异步流程。
 */
function setBusy(isBusy, message = "") {
    state.isBusy = isBusy;
    document.body.classList.toggle("is-busy", isBusy);
    if (message) {
        setStreamStatus(message);
    }
    document.querySelectorAll(
        ".top-actions button, .command-panel button, #actionHints button, #nextPageBtn",
    ).forEach((button) => {
        button.disabled = isBusy;
    });
    const userInput = document.getElementById("userInput");
    if (userInput) {
        userInput.disabled = isBusy;
    }
    renderDebugConfig();
}

/**
 * 功能：包裹长耗时操作，统一设置忙碌态并在结束或失败时释放控件。
 * 入参：message（string）：操作开始时展示的玩家可见状态；fn（Function）：待执行异步函数。
 * 出参：Promise<any>，透传 fn 的返回值。
 * 异常：fn 抛出的异常会向上透传；finally 中始终释放忙碌态。
 */
async function withBusy(message, fn) {
    if (state.isBusy) {
        throw new Error("上一项操作仍在处理中，请稍候。");
    }
    setBusy(true, message);
    try {
        return await fn();
    } finally {
        setBusy(false);
    }
}

/**
 * 功能：根据 API 返回的角色与场景快照渲染最小可玩控制台。
 * 入参：payload（object）：会话或回合响应，可包含 active_character、scene_snapshot。
 * 出参：void。
 * 异常：字段缺失时按占位文本降级。
 */
function renderPlayState(payload) {
    const previousPayload = state.lastPayload;
    state.lastPayload = payload;
    const character = payload.active_character || {};
    const scene = payload.scene_snapshot || {};
    const location = scene.current_location || {};
    const exits = Array.isArray(scene.exits) ? scene.exits : [];
    const npcs = Array.isArray(scene.visible_npcs) ? scene.visible_npcs : [];
    const items = Array.isArray(scene.visible_items) ? scene.visible_items : [];
    const quests = Array.isArray(scene.active_quests) ? scene.active_quests : [];
    const locationName = textOr(location.name || location.id, "未知地点");

    setText("topSceneName", locationName);
    setText("sceneTitle", locationName);
    setText("sceneDescription", textOr(location.description, "暂无地点描述。"));
    setText("sceneBadge", "安全区域");
    setText("topSafetyBadge", "安全区域");
    renderSceneMeta(exits, npcs, items);
    renderActionHints(payload);
    renderCharacter(character, payload);
    renderInventory(character);
    renderQuests(quests);
    renderMemoryText(payload);
    renderContextBrowser(payload);
    const hasTurnDiagnostics = payload.outcome !== undefined
        || payload.action_intent !== undefined
        || payload.final_response;
    renderAgentStatus(hasTurnDiagnostics ? payload : (previousPayload || payload));
    renderDebugConfig();
}

/**
 * 功能：渲染场景出口、NPC 与物品摘要。
 * 入参：exits（array）、npcs（array）、items（array）：场景快照子字段。
 * 出参：void。
 * 异常：DOM 缺失时静默返回。
 */
function renderSceneMeta(exits, npcs, items) {
    const sceneMeta = document.getElementById("sceneMeta");
    if (!sceneMeta) {
        return;
    }
    const exitText = exits.length
        ? exits.map((item) => item.label || item.location_id).join("、")
        : "暂无出口";
    const npcText = npcs.length
        ? npcs.map((item) => item.name || item.entity_id).join("、")
        : "无可见目标";
    const itemText = items.length
        ? items.map((item) => item.name || item.item_id).join("、")
        : "无可见物品";
    sceneMeta.innerHTML = `
        <span class="meta-chip">出口：${escapeHtml(exitText)}</span>
        <span class="meta-chip">目标：${escapeHtml(npcText)}</span>
        <span class="meta-chip">物品：${escapeHtml(itemText)}</span>
    `;
}

/**
 * 功能：渲染叙事响应返回的建议行动，避免展示静态场景建议造成选项来源混乱。
 * 入参：payload（object）：会话或回合响应，只有 quick_actions 会生成按钮。
 * 出参：void。
 * 异常：DOM 缺失时静默返回。
 */
function renderActionHints(payload) {
    const actionHints = document.getElementById("actionHints");
    if (!actionHints) {
        return;
    }
    actionHints.textContent = "";
    const suggestions = Array.isArray(payload.quick_actions) ? payload.quick_actions : [];
    suggestions.forEach((text, index) => {
        const button = document.createElement("button");
        button.type = "button";
        button.disabled = state.isBusy;
        button.textContent = `${index + 1}. ${text}`;
        button.addEventListener("click", async () => {
            try {
                await withBusy("提交行动中，等待 LLM 运算", async () => submitConfiguredTurn(text));
            } catch (error) {
                const message = error instanceof Error ? error.message : String(error);
                logStatus(`建议行动失败：${message}`);
                setStreamStatus(`失败：${message}`);
            }
        });
        actionHints.appendChild(button);
    });
}

/**
 * 功能：渲染角色 HP/MP 与基础身份。
 * 入参：character（object）：active_character；payload（object）：当前响应。
 * 出参：void。
 * 异常：数值缺失时按 0 降级，避免进度条溢出。
 */
function renderCharacter(character, payload) {
    const name = textOr(character.name, "旅行者");
    const id = textOr(character.id, "player_01");
    const hp = Number(character.hp ?? 0);
    const maxHp = Math.max(Number(character.max_hp ?? hp), 1);
    const mp = Number(character.mp ?? 0);
    const maxMp = Math.max(Number(character.max_mp ?? mp), 1);
    setText("characterName", name);
    setText("characterIdText", id);
    setText("hpText", `${hp} / ${maxHp}`);
    setText("mpText", `${mp} / ${maxMp}`);
    setText("characterMode", payload.is_sandbox_mode ? "Shadow" : "Active");
    const hpBar = document.getElementById("hpBar");
    const mpBar = document.getElementById("mpBar");
    if (hpBar) {
        hpBar.style.width = `${Math.max(0, Math.min(100, (hp / maxHp) * 100))}%`;
    }
    if (mpBar) {
        mpBar.style.width = `${Math.max(0, Math.min(100, (mp / maxMp) * 100))}%`;
    }
}

/**
 * 功能：优先用 inventory_items 渲染可读背包，缺失时降级显示 inventory 内部 ID。
 * 入参：character（object）：active_character，inventory_items 由 Web 展示层补全。
 * 出参：void。
 * 异常：DOM 缺失时静默返回。
 */
function renderInventory(character) {
    const inventoryGrid = document.getElementById("inventoryGrid");
    const inventoryItems = Array.isArray(character.inventory_items)
        ? character.inventory_items
        : [];
    const inventory = inventoryItems.length
        ? inventoryItems
        : (Array.isArray(character.inventory) ? character.inventory : []);
    setText("inventoryCount", `${inventory.length}`);
    if (!inventoryGrid) {
        return;
    }
    if (!inventory.length) {
        inventoryGrid.innerHTML = '<div class="empty-state">暂无物品信息</div>';
        return;
    }
    inventoryGrid.innerHTML = inventory.map((item) => {
        const itemId = typeof item === "string" ? item : item.item_id;
        const name = typeof item === "string" ? item : (item.name || item.item_id);
        const description = typeof item === "string"
            ? "物品目录未命中，暂以内部 ID 显示。"
            : (item.description || item.item_type || "暂无物品描述。");
        return `
        <div class="inventory-item">
            <strong>${escapeHtml(name)}</strong>
            <span>${escapeHtml(description)}</span>
            <small>${escapeHtml(itemId || "")}</small>
        </div>
    `;
    }).join("");
}

/**
 * 功能：渲染当前任务列表。
 * 入参：quests（array）：scene_snapshot.active_quests。
 * 出参：void。
 * 异常：DOM 缺失时静默返回。
 */
function renderQuests(quests) {
    const questList = document.getElementById("questList");
    if (!questList) {
        return;
    }
    if (!quests.length) {
        questList.innerHTML = '<div class="empty-state">暂无活跃任务</div>';
        return;
    }
    questList.innerHTML = quests.map((quest) => `
        <div class="quest-card">
            <strong>${escapeHtml(quest.name || quest.id || "未命名任务")}</strong>
            <p>${escapeHtml(quest.description || quest.status || "等待进一步推进。")}</p>
        </div>
    `).join("");
}

/**
 * 功能：渲染记忆摘要到角色栏和调试栏。
 * 入参：payload（object）：当前会话或回合响应。
 * 出参：void。
 * 异常：DOM 缺失时静默返回。
 */
function renderMemoryText(payload) {
    const memory = payload.memory_summary || payload.scene_snapshot?.recent_memory || "";
    setText("memorySummaryText", memory || "暂无记忆摘要。");
}

/**
 * 功能：渲染上下文浏览窗口，方便调试当前场景、记忆与 trace。
 * 入参：payload（object）：会话或回合响应。
 * 出参：void。
 * 异常：DOM 缺失时静默返回；JSON 序列化异常向上抛出。
 *     当 payload.debug_trace 为空时，保留最近一次非空轨迹，避免被会话详情接口覆盖。
 */
function renderContextBrowser(payload) {
    const trace = Array.isArray(payload.debug_trace) ? payload.debug_trace : [];
    if (trace.length) {
        state.lastNonEmptyTrace = trace;
    }
    renderJson("contextScene", {
        scene_snapshot: payload.scene_snapshot || {},
        active_character: payload.active_character || {},
    });
    setText("contextMemory", payload.memory_summary || payload.scene_snapshot?.recent_memory || "");
    renderJson("contextTrace", state.lastNonEmptyTrace);
}

/**
 * 功能：根据最近一次响应生成 Agent 调试卡片。
 * 入参：payload（object）：当前回合或会话响应。
 * 出参：void。
 * 异常：DOM 缺失时静默返回。
 */
function renderAgentStatus(payload) {
    const el = document.getElementById("agentStatus");
    if (!el) {
        return;
    }
    const intent = payload.action_intent || {};
    const trace = Array.isArray(payload.debug_trace) ? payload.debug_trace : [];
    const nluStatus = payload.outcome === "clarification" ? "需要澄清" : intent.type ? "已解析" : "等待输入";
    const gmStatus = payload.final_response ? "已生成响应" : "等待回合";
    const memoryStatus = payload.should_write_story_memory ? "本回合写入" : "未写入";
    const outerStatus = payload.should_advance_turn ? "已允许推进" : "未推进";
    const cards = [
        ["NLUAgent", nluStatus, `type=${intent.type || "none"}；question=${payload.clarification_question || "无"}`],
        ["GMAgent", gmStatus, payload.final_response || "暂无叙事输出"],
        ["Memory", memoryStatus, `summary_length=${String(payload.memory_summary || "").length}`],
        ["OuterLoop", outerStatus, "详细投递结果请查看后端运行日志。"],
        ["Trace", `${trace.length} 条`, trace.map((item) => item.stage || item.status).filter(Boolean).join(" / ") || "暂无"],
    ];
    el.innerHTML = cards.map(([name, status, detail]) => `
        <article class="agent-card">
            <strong>${escapeHtml(name)}<span>${escapeHtml(status)}</span></strong>
            <p>${escapeHtml(detail)}</p>
        </article>
    `).join("");
}

/**
 * 功能：渲染当前前端配置与请求状态。
 * 入参：无。
 * 出参：void。
 * 异常：DOM 缺失时静默返回。
 */
function renderDebugConfig() {
    renderJson("debugConfig", {
        sessionId: state.sessionId,
        page: state.page,
        pageSize: state.pageSize,
        requestCounter: state.requestCounter,
        lastSseEvent: state.lastSseEvent,
        lastRequest: state.lastRequest,
        debugVisible: state.debugVisible,
        outputMode: state.outputMode,
        isBusy: state.isBusy,
    });
}

/**
 * 功能：同步输出方式配置控件和运行时状态。
 * 入参：无，读取 state.outputMode 并写入 DOM。
 * 出参：void。
 * 异常：DOM 缺失时静默返回，仍保留默认流式输出配置。
 */
function renderOutputModeConfig() {
    const select = document.getElementById("outputModeSelect");
    if (!select) {
        return;
    }
    select.value = state.outputMode === "sync" ? "sync" : "stream";
}

/**
 * 功能：更新回合输出方式并持久化到浏览器本地配置。
 * 入参：mode（string）：stream 或 sync，其他值会降级为 stream。
 * 出参：void。
 * 异常：localStorage 写入失败时仅记录日志，不影响本次页面使用。
 */
function setOutputMode(mode) {
    state.outputMode = mode === "sync" ? "sync" : "stream";
    try {
        localStorage.setItem("tre.outputMode", state.outputMode);
    } catch {
        logStatus("输出方式配置未能写入浏览器本地存储。");
    }
    setStreamStatus(state.outputMode === "stream" ? "流式输出已启用" : "普通输出已启用");
    renderOutputModeConfig();
    renderDebugConfig();
}

/**
 * 功能：更新流式回合状态栏。
 * 入参：message（string）：要展示的阶段文本。
 * 出参：void。
 * 异常：DOM 缺失时静默返回。
 */
function setStreamStatus(message) {
    setText("streamStatus", message);
}

/**
 * 功能：向对话区追加一条消息。
 * 入参：role（string）：system/player/gm/clarify；title（string）：标题；text（string）：正文。
 * 出参：void。
 * 异常：DOM 缺失时仅更新内存记录。
 */
function appendConversation(role, title, text) {
    state.conversation.push({role, title, text, time: new Date().toLocaleTimeString()});
    state.conversation = state.conversation.slice(-30);
    renderConversation();
    return state.conversation.length - 1;
}

/**
 * 功能：更新指定对话项文本，用于把 GM 流式片段逐步写入同一条消息。
 * 入参：index（number）：state.conversation 下标；text（string）：新正文。
 * 出参：void。
 * 异常：下标无效时静默返回，避免流式乱序中断页面。
 */
function updateConversationText(index, text) {
    if (!Number.isInteger(index) || index < 0 || index >= state.conversation.length) {
        return;
    }
    state.conversation[index].text = text;
    renderConversation();
}

/**
 * 功能：重绘对话记录，保留最近 30 条。
 * 入参：无。
 * 出参：void。
 * 异常：DOM 缺失时静默返回。
 */
function renderConversation() {
    const el = document.getElementById("conversationLog");
    if (!el) {
        return;
    }
    if (!state.conversation.length) {
        return;
    }
    el.innerHTML = state.conversation.map((item) => {
        const className = item.role === "player"
            ? "message player-message"
            : item.role === "clarify"
                ? "message clarify-message"
                : "message system-message";
        return `
            <article class="${className}">
                <div class="message-avatar">${escapeHtml(item.title.slice(0, 2))}</div>
                <div class="message-body">
                    <strong>${escapeHtml(item.title)} <span>${escapeHtml(item.time)}</span></strong>
                    <p>${escapeHtml(item.text)}</p>
                </div>
            </article>
        `;
    }).join("");
    el.scrollTop = el.scrollHeight;
}

/**
 * 功能：统一处理回合响应，更新游戏区、角色区和调试区。
 * 入参：data（object）：API 回合响应；userInput（string）：玩家输入。
 * 出参：void。
 * 异常：字段缺失时按占位降级。
 */
function renderTurnPayload(data, userInput) {
    renderJson("turnResult", data);
    renderPlayState(data);
    appendConversation("player", "你", userInput);
    if (data.outcome === "clarification" && data.clarification_question) {
        appendConversation("clarify", "系统", data.clarification_question);
    } else {
        appendConversation("gm", "系统", data.final_response || "回合已处理。");
    }
}

/**
 * 功能：刷新并渲染会话概览。
 * 入参：void。
 * 出参：Promise<void>。
 * 异常：会话不存在或接口异常时抛出 Error，由调用方统一捕获。
 */
async function refreshSessionInfo() {
    if (!state.sessionId) {
        throw new Error("请先创建或加载会话。");
    }
    const data = await callApi(`/api/sessions/${state.sessionId}`);
    renderJson("sessionInfo", data);
    renderPlayState(data);
}

/**
 * 功能：创建新会话并设置当前会话上下文，同时展示后端返回的 GM 开场叙事。
 * 入参：void。
 * 出参：Promise<void>。
 * 异常：接口异常时抛出 Error，由调用方统一捕获。
 */
async function createSession() {
    setStreamStatus("创建会话中，等待 GM 生成开场叙事");
    const characterId = document.getElementById("characterId").value.trim() || "player_01";
    const payload = {
        request_id: nextRequestId("create"),
        character_id: characterId,
        sandbox_mode: false,
    };
    const data = await callApi("/api/sessions", {
        method: "POST",
        body: JSON.stringify(payload),
    });
    state.sessionId = data.session_id;
    state.page = 1;
    state.lastNonEmptyTrace = [];
    document.getElementById("sessionIdInput").value = state.sessionId;
    renderJson("sessionInfo", data);
    renderPlayState(data);
    appendConversation(
        "gm",
        "系统",
        data.final_response || `进入场景：${data.scene_snapshot?.current_location?.name || "未知地点"}`,
    );
    logStatus(`会话创建成功：${state.sessionId}`);
}

/**
 * 功能：根据输入框中的会话 ID 加载现有会话。
 * 入参：void。
 * 出参：Promise<void>。
 * 异常：输入为空或接口异常时抛出 Error，由调用方统一捕获。
 */
async function loadSession() {
    setStreamStatus("加载会话中");
    const sessionId = document.getElementById("sessionIdInput").value.trim();
    if (!sessionId) {
        throw new Error("请输入会话ID后再加载。");
    }
    state.sessionId = sessionId;
    state.page = 1;
    state.lastNonEmptyTrace = [];
    await refreshSessionInfo();
    appendConversation("system", "系统", `会话已加载：${state.sessionId}`);
    logStatus(`会话加载成功：${state.sessionId}`);
}

/**
 * 功能：提交一回合输入并刷新历史视图，回合视图直接使用本次叙事响应。
 * 入参：inputOverride（string，默认空）：快捷选项直接提交文本；为空时读取输入框。
 * 出参：Promise<void>。
 * 异常：参数校验失败或接口异常时抛出 Error，由调用方统一捕获。
 */
async function submitTurn(inputOverride = "") {
    if (!state.sessionId) {
        throw new Error("请先创建或加载会话。");
    }
    const userInput = (inputOverride || document.getElementById("userInput").value).trim();
    if (!userInput) {
        throw new Error("请输入回合文本。");
    }
    setStreamStatus("提交行动中，等待 GM 生成叙事");
    const payload = {
        request_id: nextRequestId("turn"),
        user_input: userInput,
    };
    const data = await callApi(`/api/sessions/${state.sessionId}/turns`, {
        method: "POST",
        body: JSON.stringify(payload),
    });
    renderTurnPayload(data, userInput);
    document.getElementById("userInput").value = "";
    await listTurns();
    logStatus(`回合提交成功：turn_id=${data.turn_id}`);
}

/**
 * 功能：按可视化配置提交回合，玩家只需要点击一个发送按钮。
 * 入参：inputOverride（string，默认空）：快捷选项直接提交文本；为空时读取输入框。
 * 出参：Promise<void>。
 * 异常：普通或流式提交异常由对应函数抛出，再由按钮包装器展示。
 */
async function submitConfiguredTurn(inputOverride = "") {
    if (state.outputMode === "sync") {
        await submitTurn(inputOverride);
        return;
    }
    await submitTurnStream(inputOverride);
}

/**
 * 功能：通过 SSE 风格流式接口提交回合，并在运算阶段给玩家反馈。
 * 入参：inputOverride（string，默认空）：快捷选项直接提交文本；为空时读取输入框。
 * 出参：Promise<void>。
 * 异常：网络异常或流式错误事件会抛出 Error，由调用方统一捕获。
 */
async function submitTurnStream(inputOverride = "") {
    if (!state.sessionId) {
        throw new Error("请先创建或加载会话。");
    }
    const userInput = (inputOverride || document.getElementById("userInput").value).trim();
    if (!userInput) {
        throw new Error("请输入回合文本。");
    }
    setStreamStatus("发送回合输入");
    appendConversation("player", "你", userInput);
    state.streamingGmText = "";
    state.streamingGmIndex = null;
    const body = JSON.stringify({
        request_id: nextRequestId("turns"),
        user_input: userInput,
    });
    state.lastRequest = {
        url: `/api/sessions/${state.sessionId}/turns/stream`,
        method: "POST",
        body,
    };
    logStatus(`SSE POST ${state.lastRequest.url}`);
    const response = await fetch(state.lastRequest.url, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body,
    });
    if (!response.ok || !response.body) {
        throw new Error(`HTTP ${response.status}`);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
        const {value, done} = await reader.read();
        if (done) {
            break;
        }
        buffer += decoder.decode(value, {stream: true});
        const chunks = buffer.split("\n\n");
        buffer = chunks.pop() || "";
        for (const chunk of chunks) {
            const event = parseSseChunk(chunk);
            if (!event) {
                continue;
            }
            handleStreamEvent(event.name, event.payload, userInput);
        }
    }
}

/**
 * 功能：解析一个 SSE 文本块。
 * 入参：chunk（string）：形如 event/data 的 SSE 块。
 * 出参：{name: string, payload: object} | null，解析失败返回 null。
 * 异常：JSON 解析异常内部捕获并降级为 null。
 */
function parseSseChunk(chunk) {
    const eventLine = chunk.split("\n").find((line) => line.startsWith("event:"));
    const dataLine = chunk.split("\n").find((line) => line.startsWith("data:"));
    if (!eventLine || !dataLine) {
        return null;
    }
    try {
        return {
            name: eventLine.replace("event:", "").trim(),
            payload: JSON.parse(dataLine.replace("data:", "").trim()),
        };
    } catch {
        return null;
    }
}

/**
 * 功能：处理流式接口事件，更新状态栏和最终回合视图。
 * 入参：name（string）：事件名；payload（object）：事件数据；userInput（string）：玩家输入。
 * 出参：void。
 * 异常：error 事件会抛出 Error，交由调用方统一展示。
 */
function handleStreamEvent(name, payload, userInput) {
    const labels = {
        received: "已收到输入",
        loading_scene: "读取场景中",
        loading_scene_detail: "场景快照明细",
        parsing_nlu: "理解玩家意图中",
        parsing_nlu_detail: "NLU 解析明细",
        validating_action: "校验动作中",
        validating_action_detail: "动作校验明细",
        resolving_action: "执行确定性结算中",
        resolving_action_detail: "结算结果明细",
        rendering_gm: "生成叙事中",
        gm_delta: "叙事输出中",
    };
    state.lastSseEvent = name;
    const detailText = payload.detail ? ` | detail=${JSON.stringify(payload.detail)}` : "";
    logStatus(`SSE ${name}: ${payload.message || "收到事件"}${detailText}`);
    renderDebugConfig();
    if (name === "gm_delta") {
        const delta = payload.delta || "";
        state.streamingGmText += delta;
        if (state.streamingGmIndex === null) {
            state.streamingGmIndex = appendConversation("gm", "系统", state.streamingGmText);
        } else {
            updateConversationText(state.streamingGmIndex, state.streamingGmText);
        }
        setStreamStatus(labels[name]);
        return;
    }
    if (name === "error") {
        state.streamingGmText = "";
        state.streamingGmIndex = null;
        renderPlayerFacingError(payload.message || "流式回合失败", payload);
        throw new Error(payload.message || "流式回合失败");
    }
    if (name === "done") {
        setStreamStatus("回合完成");
        if (state.streamingGmIndex === null) {
            renderJson("turnResult", payload);
            renderPlayState(payload);
            if (payload.outcome === "clarification" && payload.clarification_question) {
                appendConversation("clarify", "系统", payload.clarification_question);
            } else {
                appendConversation("gm", "系统", payload.final_response || "回合已处理。");
            }
        } else {
            renderJson("turnResult", payload);
            renderPlayState(payload);
            updateConversationText(
                state.streamingGmIndex,
                payload.final_response || state.streamingGmText || "回合已处理。",
            );
        }
        state.streamingGmText = "";
        state.streamingGmIndex = null;
        document.getElementById("userInput").value = "";
        listTurns();
        return;
    }
    setStreamStatus(labels[name] || payload.message || name);
}

/**
 * 功能：分页查询回合历史并渲染。
 * 入参：void。
 * 出参：Promise<void>。
 * 异常：会话未就绪或接口异常时抛出 Error，由调用方统一捕获。
 */
async function listTurns() {
    if (!state.sessionId) {
        throw new Error("请先创建或加载会话。");
    }
    setStreamStatus("读取历史中");
    const data = await callApi(
        `/api/sessions/${state.sessionId}/turns?page=${state.page}&page_size=${state.pageSize}`,
    );
    renderJson("turnHistory", data);
}

/**
 * 功能：读取当前会话记忆摘要。
 * 入参：void。
 * 出参：Promise<void>。
 * 异常：会话未就绪或接口异常时抛出 Error，由调用方统一捕获。
 */
async function getMemory() {
    if (!state.sessionId) {
        throw new Error("请先创建或加载会话。");
    }
    setStreamStatus("读取记忆中");
    const data = await callApi(`/api/sessions/${state.sessionId}/memory?format=summary`);
    renderJson("memoryResult", data);
    setText("memorySummaryText", data.summary || "暂无记忆摘要。");
}

/**
 * 功能：触发记忆摘要刷新并渲染结果。
 * 入参：void。
 * 出参：Promise<void>。
 * 异常：会话未就绪或接口异常时抛出 Error，由调用方统一捕获。
 */
async function refreshMemory() {
    if (!state.sessionId) {
        throw new Error("请先创建或加载会话。");
    }
    setStreamStatus("刷新记忆中，等待摘要生成");
    const payload = {request_id: nextRequestId("memory"), max_turns: 20};
    const data = await callApi(`/api/sessions/${state.sessionId}/memory/refresh`, {
        method: "POST",
        body: JSON.stringify(payload),
    });
    renderJson("memoryResult", data);
    setText("memorySummaryText", data.summary || "记忆已刷新。");
    await refreshSessionInfo();
}

/**
 * 功能：执行沙盒并入或回滚动作。
 * 入参：action（"commit"|"discard"）动作类型。
 * 出参：Promise<void>。
 * 异常：会话未就绪、动作非法或接口异常时抛出 Error，由调用方统一捕获。
 */
async function runSandboxAction(action) {
    if (!state.sessionId) {
        throw new Error("请先创建或加载会话。");
    }
    const endpoint = action === "commit" ? "commit" : "discard";
    setStreamStatus(endpoint === "commit" ? "正在并入沙盒到主线" : "正在回滚沙盒变化");
    const payload = {request_id: nextRequestId(`sandbox_${endpoint}`)};
    const data = await callApi(`/api/sessions/${state.sessionId}/sandbox/${endpoint}`, {
        method: "POST",
        body: JSON.stringify(payload),
    });
    renderJson("turnResult", data);
    renderPlayState(data);
    await refreshSessionInfo();
    await listTurns();
    logStatus(`沙盒动作完成：${endpoint}`);
}

/**
 * 功能：重置当前会话并清空页面展示状态。
 * 入参：void。
 * 出参：Promise<void>。
 * 异常：会话未就绪或接口异常时抛出 Error，由调用方统一捕获。
 */
async function resetSession() {
    if (!state.sessionId) {
        throw new Error("请先创建或加载会话。");
    }
    setStreamStatus("重置会话中");
    const payload = {
        request_id: nextRequestId("reset"),
        keep_character: true,
    };
    const data = await callApi(`/api/sessions/${state.sessionId}/reset`, {
        method: "POST",
        body: JSON.stringify(payload),
    });
    state.lastNonEmptyTrace = [];
    renderJson("turnResult", data);
    renderJson("turnHistory", {items: []});
    renderJson("memoryResult", {summary: ""});
    appendConversation("system", "系统", "会话已重置。");
    await refreshSessionInfo();
    logStatus("会话重置完成。");
}

/**
 * 功能：绑定调试面板 tabs。
 * 入参：无。
 * 出参：void。
 * 异常：DOM 缺失时跳过。
 */
function bindTabs() {
    document.querySelectorAll(".debug-tabs button").forEach((button) => {
        button.addEventListener("click", () => {
            const tab = button.dataset.tab;
            document.querySelectorAll(".debug-tabs button").forEach((item) => {
                item.classList.toggle("active", item === button);
            });
            document.querySelectorAll(".debug-tab-panel").forEach((panel) => {
                panel.classList.toggle("active", panel.id === `tab-${tab}`);
            });
        });
    });
}

/**
 * 功能：绑定页面按钮事件并统一处理前端异常提示。
 * 入参：void。
 * 出参：void。
 * 异常：事件处理异常在内部捕获并写入状态日志，不向外抛出。
 */
function bindActions() {
    const wrap = (fn, busyMessage = "处理中") => async () => {
        try {
            await withBusy(busyMessage, fn);
        } catch (error) {
            const message = error instanceof Error ? error.message : String(error);
            logStatus(`操作失败：${message}`);
            setStreamStatus(`失败：${message}`);
        }
    };

    document.getElementById("createSessionBtn").addEventListener(
        "click",
        wrap(createSession, "创建会话中，等待 GM 生成开场叙事"),
    );
    document.getElementById("loadSessionBtn").addEventListener(
        "click",
        wrap(loadSession, "加载会话中"),
    );
    document.getElementById("submitTurnBtn").addEventListener(
        "click",
        wrap(submitConfiguredTurn, "提交行动中，等待 LLM 运算"),
    );
    document.getElementById("listTurnsBtn").addEventListener(
        "click",
        wrap(listTurns, "读取历史中"),
    );
    document.getElementById("getMemoryBtn").addEventListener(
        "click",
        wrap(getMemory, "读取记忆中"),
    );
    document.getElementById("refreshMemoryBtn").addEventListener(
        "click",
        wrap(refreshMemory, "刷新记忆中"),
    );
    document.getElementById("commitSandboxBtn").addEventListener(
        "click",
        wrap(async () => runSandboxAction("commit"), "正在并入沙盒到主线"),
    );
    document.getElementById("discardSandboxBtn").addEventListener(
        "click",
        wrap(async () => runSandboxAction("discard"), "正在回滚沙盒变化"),
    );
    document.getElementById("resetBtn").addEventListener(
        "click",
        wrap(resetSession, "重置会话中"),
    );
    document.getElementById("nextPageBtn").addEventListener(
        "click",
        wrap(async () => {
            state.page += 1;
            await listTurns();
        }, "读取下一页历史中"),
    );
    document.getElementById("debugToggleBtn").addEventListener("click", () => {
        state.debugVisible = !state.debugVisible;
        document.getElementById("debugConsole").style.display = state.debugVisible ? "" : "none";
        setText("debugToggleBtn", `调试：${state.debugVisible ? "开" : "关"}`);
        renderDebugConfig();
    });
    const outputModeSelect = document.getElementById("outputModeSelect");
    if (outputModeSelect) {
        outputModeSelect.addEventListener("change", () => {
            setOutputMode(outputModeSelect.value);
        });
    }
}

bindTabs();
bindActions();
renderOutputModeConfig();
renderDebugConfig();
renderAgentStatus({});
appendConversation("system", "系统", "页面已加载，可创建或加载会话。");
logStatus("页面已加载，可开始试玩。");

window.addEventListener("error", (event) => {
    logStatus(`前端脚本错误：${event.message}`);
});

window.addEventListener("unhandledrejection", (event) => {
    const reason = event.reason instanceof Error ? event.reason.message : String(event.reason);
    logStatus(`前端异步错误：${reason}`);
});
