// Isolated headless UI acceptance against verify_archive_live.py --browser-qa.
// NODE_PATH may point to the bundled workspace dependencies. No real accounts or APIs.
const { chromium } = require("playwright");
const fs = require("node:fs/promises");
const path = require("node:path");
const crypto = require("node:crypto");

(async () => {
  const [url, directory] = process.argv.slice(2);
  if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(url)) throw new Error("Loopback URL required");
  const output = path.resolve(directory);
  if (!output.includes(path.sep + ".artifacts" + path.sep)) throw new Error("QA output only");
  const browser = await chromium.launch({ channel: "msedge", headless: true });
  const errors = [];
  try {
    const context = await browser.newContext({ viewport: { width: 1360, height: 980 }, acceptDownloads: true, reducedMotion: "reduce" });
    const page = await context.newPage();
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(url + "/app/");
    await page.getByLabel("用户名", { exact: true }).fill("archive_qa");
    await page.getByLabel("密码", { exact: true }).fill("Synthetic-archive-QA-2026");
    await page.getByRole("button", { name: "登录", exact: true }).click();
    await page.getByRole("button", { name: "归档与文件目录", exact: true }).click();
    await page.getByRole("heading", { name: "我的归档任务" }).waitFor();
    await page.getByLabel("允许发送给云端 AI 的信息").selectOption("summary");
    await page.getByRole("button", { name: "保存授权设置" }).click();
    await page.getByRole("status").filter({ hasText: "授权设置已保存" }).waitFor();
    const filename = `browser-notes-${Date.now()}.txt`;
    const source = path.join(output, filename);
    const payload = Buffer.from("Synthetic Python learning notes from actual browser upload.\n".repeat(180));
    await fs.writeFile(source, payload);
    await page.getByLabel("选择归档文件", { exact: true }).setInputFiles(source);
    await page.getByRole("button", { name: "上传归档副本", exact: true }).click();
    await page.getByRole("heading", { name: filename, exact: true }).waitFor({ timeout: 30000 });
    const item = page.locator("article").filter({ has: page.getByRole("heading", { name: filename, exact: true }) });
    const downloadPromise = page.waitForEvent("download");
    await item.getByRole("link", { name: "读取原始文件" }).click();
    const download = await downloadPromise;
    const destination = path.join(output, "browser-downloaded-notes.txt");
    await download.saveAs(destination);
    const saved = await fs.readFile(destination);
    if (!saved.equals(payload)) throw new Error("Downloaded bytes differ");
    await page.screenshot({ path: path.join(output, "browser-archive-desktop.png"), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.waitForFunction(()=>document.querySelector(".sidebar").getBoundingClientRect().right<=1);
    const mobileOverflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    if (mobileOverflow) {
      await page.screenshot({path:path.join(output,"mobile-overflow.png")});
      console.log(await page.evaluate(()=>[...document.querySelectorAll("main *")].map(e=>({tag:e.tagName,cls:e.className,right:e.getBoundingClientRect().right,width:e.getBoundingClientRect().width,text:e.textContent?.slice(0,70)})).filter(e=>e.right>window.innerWidth+2).slice(0,18)));
      throw new Error("Mobile page body overflows viewport");
    }
    await page.screenshot({ path: path.join(output, "browser-archive-mobile.png") });
    await page.getByRole("button", { name: "切换主题" }).click();
    await page.screenshot({ path: path.join(output, "browser-archive-dark.png") });
    const admin = await context.newPage();
    admin.on("pageerror", (e) => errors.push(e.message));
    await admin.goto(url + "/admin/");
    await admin.getByLabel("用户名", { exact: true }).fill("archive_admin");
    await admin.getByLabel("密码", { exact: true }).fill("Synthetic-archive-QA-2026");
    await admin.getByRole("button", { name: "登录", exact: true }).click();
    await admin.getByRole("button", { name: "AI 与归档", exact: true }).click();
    await admin.getByRole("heading", { name: "AI 管理中心", exact: true }).waitFor();
    if (await admin.getByLabel("模型 ID").inputValue() !== "deepseek-flash") throw new Error("Unexpected model");
    if (await admin.getByLabel("更新 API 密钥").inputValue() !== "") throw new Error("Key disclosure");
    await admin.getByRole("button", { name: "测试模型列表连接" }).click();
    await admin.getByRole("alert").filter({ hasText: "请选择 DeepSeek 后再检查模型列表" }).waitFor();
    await admin.screenshot({ path: path.join(output, "browser-archive-admin.png"), fullPage: true });
    if (errors.length) throw new Error(errors.join("; "));
    const report = { browser: "headless Microsoft Edge, isolated context", real_http: true,
      upload_to_catalog: "COMPLETED", download_bytes: saved.length,
      download_sha256: crypto.createHash("sha256").update(saved).digest("hex"),
      mobile_viewport: "390x844", mobile_body_overflow: mobileOverflow, dark_theme_checked: true,
      admin_live_data: true, mock_model_list_blocked: true, js_errors: errors,
      real_deepseek_called: false, baidu_connected: false };
    await fs.writeFile(path.join(output, "browser-report.json"), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
    await fs.writeFile(path.join(output, "stop-qa"), "completed");
  } finally { await browser.close(); }
})().catch((error) => { console.error(error); process.exitCode = 1; });
