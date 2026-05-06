const { chromium, request } = require('playwright');
const fs = require('fs');

(async () => {
  const result = { ts: new Date().toISOString(), steps: [], sessionId: null, play: {}, app: {}, errors: [] };

  const api = await request.newContext({ baseURL: 'http://127.0.0.1:5000' });
  const createResp = await api.post('/api/sessions', { data: { request_id: `req_browser_${Date.now()}`, character_id: 'player_01' } });
  const createJson = await createResp.json();
  result.sessionId = createJson.session_id || '';
  result.steps.push('api_create_session_ok');

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();

  const pagePlay = await context.newPage();
  try {
    await pagePlay.goto('http://127.0.0.1:5000/play', { waitUntil: 'domcontentloaded' });
    result.steps.push('open_play_ok');

    await pagePlay.fill('#sessionIdInput', result.sessionId);
    await pagePlay.click('#loadSessionBtn');
    await pagePlay.waitForTimeout(1800);
    result.steps.push('play_load_same_session_ok');

    await pagePlay.fill('#userInput', '观察周围');
    await pagePlay.click('#submitTurnBtn');
    await pagePlay.waitForTimeout(5000);
    const status = await pagePlay.$eval('#streamStatus', (el) => (el.textContent || '').trim());
    result.play.streamStatus = status;
    result.steps.push('play_turn_ok');

    const sessionInfo = await pagePlay.$eval('#sessionInfo', (el) => (el.textContent || '').trim());
    result.play.sessionInfo = sessionInfo.slice(0, 1200);
    await pagePlay.screenshot({ path: 'D:/bupuy/Documents/llmtre/test_runs/play-smoke.png', fullPage: true });
  } catch (e) {
    result.errors.push(`play_error:${e.message}`);
  }

  const pageApp = await context.newPage();
  try {
    await pageApp.goto('http://127.0.0.1:5000/app', { waitUntil: 'domcontentloaded' });
    result.steps.push('open_app_ok');

    const inputs = await pageApp.locator('input').all();
    if (inputs.length >= 2) {
      await inputs[1].fill(result.sessionId);
      await pageApp.waitForTimeout(300);
    }
    await pageApp.getByRole('button', { name: '加载' }).click();
    await pageApp.waitForTimeout(1600);
    result.steps.push('app_load_same_session_ok');

    await pageApp.locator('select').selectOption('sync');
    await pageApp.getByPlaceholder('输入行动').fill('检查周围');
    await pageApp.getByRole('button', { name: '发送' }).click();
    await pageApp.waitForTimeout(3000);
    result.steps.push('app_sync_turn_ok');

    await pageApp.locator('select').selectOption('stream');
    await pageApp.getByPlaceholder('输入行动').fill('等待片刻');
    await pageApp.getByRole('button', { name: '发送' }).click();
    await pageApp.waitForTimeout(6000);
    result.steps.push('app_stream_turn_ok');

    const traceText = await pageApp.locator('p:has-text("trace_id:")').first().textContent();
    result.app.traceText = (traceText || '').trim();
    const debugTrace = await pageApp.locator('h3:has-text("turn debug_trace") + pre').first().textContent();
    result.app.debugTrace = (debugTrace || '').slice(0, 1200);
    await pageApp.screenshot({ path: 'D:/bupuy/Documents/llmtre/test_runs/app-smoke.png', fullPage: true });
  } catch (e) {
    result.errors.push(`app_error:${e.message}`);
  }

  await browser.close();
  fs.writeFileSync('D:/bupuy/Documents/llmtre/test_runs/a1-browser-smoke.json', JSON.stringify(result, null, 2), 'utf8');
  console.log(JSON.stringify(result));
})();
