// Real Edge/Chrome UI, isolated loopback account. No simulated success responses.
const { chromium } = require('playwright');
const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
(async () => {
  const [url, output] = process.argv.slice(2);
  if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(url) || !path.resolve(output).includes(path.sep + '.artifacts' + path.sep)) throw new Error('Isolated loopback required');
  const edge = await chromium.launch({ channel: 'msedge', headless: true });
  const chrome = await chromium.launch({ channel: 'chrome', headless: true });
  const errors = [];
  try {
    const contexts = await Promise.all([edge.newContext({ acceptDownloads: true, viewport: {width:1440,height:1000} }), chrome.newContext({ acceptDownloads: true, viewport: {width:1440,height:1000} })]);
    const pages = await Promise.all(contexts.map(c => c.newPage()));
    for (let i = 0; i < pages.length; i++) {
      const p = pages[i]; p.on('pageerror', e => errors.push(e.message));
      await p.goto(url + '/app/');
      await p.getByLabel('用户名', { exact: true }).fill('v25_browser');
      await p.getByLabel('密码', { exact: true }).fill('Synthetic-v25-browser');
      await p.getByRole('button', { name: '登录', exact: true }).click();
      await p.getByRole('button', { name: 'DeviceLink 互传', exact: true }).click();
      await p.getByLabel('当前浏览器设备名称').fill('Browser-' + (i ? 'Chrome' : 'Edge'));
      await p.getByRole('button', { name: '绑定并开启接收', exact: true }).click();
      await p.getByText('当前绑定：', { exact: false }).waitFor();
    }
    const payload = Buffer.from('Synthetic cross-browser transport\n'.repeat(18000));
    async function send(p, target, filename) {
      await p.getByLabel('在线可接收设备').selectOption({ label: target });
      await p.getByLabel('选择发送文件').setInputFiles({ name: filename, mimeType: 'application/octet-stream', buffer: payload });
      await p.getByRole('button', { name: '发起发送', exact: true }).click();
    }
    async function receive(p, filename, expectedHash) {
      const row = p.locator('tr').filter({ hasText: filename }).first();
      await row.getByRole('button', { name: '接受并接收', exact: true }).click();
      await p.getByRole('heading', { name: '接收字节已校验，尚未确认', exact: true }).waitFor();
      const downloading = p.waitForEvent('download');
      await p.getByRole('link', { name: '保存副本', exact: true }).click();
      const download = await downloading;
      const saved = path.join(output, filename + '-saved'); await download.saveAs(saved);
      const got = crypto.createHash('sha256').update(await fs.readFile(saved)).digest('hex');
      if (got !== expectedHash) throw new Error('Downloaded hash mismatch');
      await p.getByRole('button', { name: '确认字节接收', exact: true }).click();
      await p.getByRole('heading', { name: '已确认字节；请检查保存副本', exact: true }).waitFor();
      await p.screenshot({ path: path.join(output, filename + '.png'), fullPage: true });
    }
    const expected = crypto.createHash('sha256').update(payload).digest('hex');
    await pages[0].getByLabel('在线可接收设备').locator('option').filter({ hasText: 'Browser-Chrome' }).waitFor({state:'attached'});
    await send(pages[0], 'Browser-Chrome (browser)', 'edge-to-chrome.bin');
    await receive(pages[1], 'edge-to-chrome.bin', expected);
    await pages[0].getByRole('status').filter({ hasText: '传输已完成' }).waitFor();
    await send(pages[0], 'Native-Windows (windows)', 'browser-to-windows.bin');
    const nativeHash = crypto.createHash('sha256').update(Buffer.from('Synthetic Windows native return\n'.repeat(12000))).digest('hex');
    await receive(pages[0], 'windows-to-browser.bin', nativeHash);
    const report = { date: '2026-10-31', Edge: edge.version(), Chrome: chrome.version(),
      scope: 'one Windows host; actual UI + Python Windows HTTP client; loopback TCP',
      browser_to_browser: true, browser_to_windows_http_client: true,
      windows_http_client_to_browser: true, saved_bytes_hash_verified: true,
      browser_file_bytes: payload.length, physical_windows_hosts: 1,
      physical_ios: false, physical_android: false, public_https: false, production: false,
      javascript_errors: errors };
    if (errors.length) throw new Error('Browser page errors');
    await fs.writeFile(path.join(output, 'browser-report.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
  } finally { await edge.close(); await chrome.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
