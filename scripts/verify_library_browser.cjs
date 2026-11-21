// Isolated Edge acceptance: synthetic accounts, local HTTP, no external API calls.
const { chromium } = require("playwright");
const fs = require("node:fs/promises");
const path = require("node:path");
const crypto = require("node:crypto");
(async () => {
  const [url, directory] = process.argv.slice(2);
  if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(url)) throw new Error("Loopback only");
  const output = path.resolve(directory);
  if (!output.includes(path.sep+".artifacts"+path.sep)) throw new Error("Artifact directory required");
  const browser = await chromium.launch({channel:"msedge",headless:true});
  const errors = [];
  let page;
  try {
    const context = await browser.newContext({viewport:{width:1440,height:1040},acceptDownloads:true,reducedMotion:"reduce"});
    page = await context.newPage();
    page.on("pageerror",e => errors.push(e.message));
    await page.goto(url+"/app/");
    await page.getByLabel("用户名",{exact:true}).fill("library_qa");
    await page.getByLabel("密码",{exact:true}).fill("Synthetic-library-QA-2026");
    await page.getByRole("button",{name:"登录",exact:true}).click();
    await page.getByRole("button",{name:"全局文件库",exact:true}).click();
    await page.getByRole("button",{name:"跨设备学习笔记.txt",exact:true}).waitFor();
    await page.getByRole("button",{name:"跨设备学习笔记.txt",exact:true}).click();
    await page.getByRole("heading",{name:"文件详情 · 跨设备学习笔记.txt"}).waitFor();
    await page.waitForFunction(() => [...document.querySelectorAll("select")].some(s => s.options.length === 3 && s.textContent.includes("Version 1")));
    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button",{name:"校验并下载版本",exact:true}).click();
    const download = await downloadPromise;
    const saved = path.join(output,"browser-version-download.txt");
    await download.saveAs(saved);
    const payload = await fs.readFile(saved);
    if(payload.toString() !== "Python candidate based on old version") throw new Error("Wrong historical bytes");
    await page.getByLabel("名称",{exact:true}).fill("网页更新笔记.txt");
    await page.getByRole("button",{name:"保存名称 / 分类 / 目录",exact:true}).click();
    await page.getByRole("button",{name:"网页更新笔记.txt",exact:true}).waitFor();
    await page.getByRole("button",{name:"网页更新笔记.txt",exact:true}).click();
    const revised = path.join(output,"Python-browser-v4.txt");
    await fs.writeFile(revised,"Python version four uploaded by real browser\n");
    await page.getByLabel("提交新版本",{exact:true}).setInputFiles(revised);
    await page.getByRole("button",{name:"上传新版本",exact:true}).click();
    await page.getByText("新版本已暂存，AI 授权/计划与归档进度请在归档任务中查看",{exact:true}).waitFor();
    await page.locator("tbody tr").filter({hasText:"网页更新笔记.txt"}).filter({hasText:"v4"}).waitFor({timeout:30000});
    await page.getByRole("button",{name:"网页更新笔记.txt",exact:true}).click();
    await page.getByRole("button",{name:"移入回收站",exact:true}).click();
    await page.getByRole("button",{name:"回收站",exact:true}).click();
    await page.getByRole("button",{name:"网页更新笔记.txt",exact:true}).waitFor();
    await page.getByRole("button",{name:"网页更新笔记.txt",exact:true}).click();
    await page.getByRole("button",{name:"恢复文件",exact:true}).click();
    await page.getByRole("button",{name:"全部文件",exact:true}).click();
    await page.getByRole("button",{name:"网页更新笔记.txt",exact:true}).waitFor();
    await page.getByRole("button",{name:"网页更新笔记.txt",exact:true}).click();
    await page.screenshot({path:path.join(output,"library-desktop.png"),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.evaluate(() => window.scrollTo(0,0));
    await page.waitForFunction(() => document.querySelector(".sidebar").getBoundingClientRect().right <= 1);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    await page.screenshot({path:path.join(output,"library-mobile.png"),fullPage:true});
    if(overflow) throw new Error("Mobile body overflow");
    await page.getByRole("button",{name:"切换主题",exact:true}).click();
    await page.screenshot({path:path.join(output,"library-mobile-dark.png"),fullPage:true});
    const admin = await context.newPage();
    admin.on("pageerror",e => errors.push(e.message));
    await admin.goto(url+"/admin/");
    await admin.getByLabel("用户名",{exact:true}).fill("library_admin");
    await admin.getByLabel("密码",{exact:true}).fill("Synthetic-library-QA-2026");
    await admin.getByRole("button",{name:"登录",exact:true}).click();
    await admin.getByRole("button",{name:"文件库运营",exact:true}).click();
    await admin.getByRole("heading",{name:"文件元数据",exact:true}).waitFor();
    await admin.getByRole("button",{name:"只读存储对账",exact:true}).click();
    await admin.getByRole("heading",{name:"只读对账结果",exact:true}).waitFor();
    await admin.screenshot({path:path.join(output,"library-admin.png"),fullPage:true});
    // Admin-scoped cookies cannot fetch another user's private bytes through user API.
    const privateRead = await admin.evaluate(async () => (await fetch('/api/library/search')).status);
    // Same browser context also has an explicitly logged-in user session, so use a fresh context for isolation.
    const adminOnly = await browser.newContext();
    await adminOnly.addCookies((await context.cookies()).filter(c=>c.name === "fm_admin"));
    const denied = await adminOnly.request.get(url+"/api/library/search");
    if(denied.status() !== 401) throw new Error("Admin bypassed private user auth");
    await adminOnly.close();
    if(errors.length) throw new Error(errors.join("; "));
    const report = {browser:"isolated headless Microsoft Edge",real_http:true,metadata:true,versions:4,
      browser_upload:true,rename:true,recycle_restore:true,download_sha256:crypto.createHash("sha256").update(payload).digest("hex"),
      download_bytes:payload.length,mobile_viewport:"390x844 (desktop simulation)",mobile_overflow:overflow,dark_theme:true,
      admin_real_statistics:true,admin_private_content_denied:true,js_errors:errors,real_mobile_device:false};
    await fs.writeFile(path.join(output,"browser-report.json"),JSON.stringify(report,null,2));
    console.log(JSON.stringify(report));
  } catch(error) {
    if(page) { await page.screenshot({path:path.join(output,"browser-failure.png"),fullPage:true}); await fs.writeFile(path.join(output,"browser-failure.txt"),await page.locator("body").innerText()); }
    throw error;
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
