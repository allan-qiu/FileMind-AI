// Isolated browser acceptance. Only synthetic accounts, loopback and test files.
const {chromium,webkit,devices}=require('playwright');
const fs=require('node:fs/promises');
const path=require('node:path');
const crypto=require('node:crypto');
(async()=>{
  const [url,directory]=process.argv.slice(2);
  if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(url))throw new Error('Loopback only');
  const output=path.resolve(directory);if(!output.includes(path.sep+'.artifacts'+path.sep))throw new Error('Artifacts only');
  const payload=Buffer.from('Browser-created FileMind temporary share\n'.repeat(800));
  const expected=crypto.createHash('sha256').update(payload).digest('hex');
  const engines=[['Edge',{channel:'msedge'}],['Chrome',{channel:'chrome'}]];
  const results=[];
  for(const [name,options] of engines){
    const browser=await chromium.launch({...options,headless:true});let page,activeGuest;
    const errors=[];
    try{
      const context=await browser.newContext({viewport:{width:1440,height:1100},acceptDownloads:true,permissions:['clipboard-read','clipboard-write']});
      page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
      await page.goto(url+'/app/');
      await page.getByLabel('用户名',{exact:true}).fill('share_qa');await page.getByLabel('密码',{exact:true}).fill('Synthetic-share-QA-2026');await page.getByRole('button',{name:'登录',exact:true}).click();
      await page.getByRole('button',{name:'分享中心',exact:true}).click();
      await page.getByLabel('选择本地文件（点击创建后上传）').setInputFiles({name:`browser-${name}.txt`,mimeType:'text/plain',buffer:payload});
      await page.getByLabel('最多下载次数').fill('2');await page.getByLabel('访问密码（可选）').fill('browser-pass');
      await page.getByRole('button',{name:'创建分享链接',exact:true}).click();
      const linkInput=page.getByLabel('新分享链接（仅此时显示，请保存）');await linkInput.waitFor();const shareUrl=await linkInput.inputValue();
      if(!shareUrl.startsWith(url+'/s/#'))throw new Error('Wrong share route');
      await page.getByRole('button',{name:'复制分享链接',exact:true}).click();
      if(await page.evaluate(()=>navigator.clipboard.readText())!==shareUrl)throw new Error('Clipboard mismatch');
      const row=page.locator('tr').filter({hasText:`browser-${name}.txt`}).first();
      await row.getByRole('button',{name:'修改配置',exact:true}).click();await page.getByLabel('新下载上限').fill('3');await page.getByRole('button',{name:'保存分享配置',exact:true}).click();
      await row.getByRole('button',{name:'下载记录',exact:true}).click();await page.getByRole('heading',{name:'下载历史与访问记录'}).waitFor();await page.getByRole('button',{name:'关闭记录',exact:true}).click();
      await page.screenshot({path:path.join(output,`share-owner-${name}.png`),fullPage:true,mask:[linkInput]});
      const guests=await browser.newContext({viewport:{width:1280,height:1000},acceptDownloads:true,reducedMotion:'reduce'});const guest=await guests.newPage();activeGuest=guest;guest.on('pageerror',e=>errors.push(e.message));
      guest.on('console',m=>{if(m.type()==='error')console.log('Browser console:',m.text().replace(/\/s\/#[A-Za-z0-9_-]+/g,'/s/#[REDACTED]'));});
      guest.on('requestfailed',r=>console.log('Browser request failure:',r.failure()?.errorText));
      guest.on('response',async r=>{if(r.status()===403)console.log('Browser forbidden:',new URL(r.url()).pathname,'origin=',r.request().headers().origin,'detail=',await r.text().catch(()=>''));});
      await guest.goto(shareUrl);await guest.getByLabel('访问密码',{exact:true}).fill('wrong');await guest.getByRole('button',{name:'验证访问密码'}).click();await guest.getByRole('alert').waitFor();
      // Error recovery retains a password form even when verification is rejected.
      await guest.getByLabel('访问密码',{exact:true}).fill('browser-pass');await guest.getByRole('button',{name:'验证访问密码'}).click();
      await guest.getByRole('heading',{name:`browser-${name}.txt`,exact:true}).waitFor();
      const downloading=guest.waitForEvent('download');await guest.getByRole('button',{name:'校验并下载',exact:true}).click();const download=await downloading;const saved=path.join(output,`browser-${name}-download.txt`);await download.saveAs(saved);
      const digest=crypto.createHash('sha256').update(await fs.readFile(saved)).digest('hex');if(digest!==expected)throw new Error('Browser SHA mismatch');
      await guest.getByText('SHA-256 校验一致',{exact:false}).waitFor();await guest.screenshot({path:path.join(output,`share-public-${name}.png`),fullPage:true});
      await guest.setViewportSize({width:390,height:844});await guest.getByRole('button',{name:'切换深色'}).click();
      const overflow=await guest.evaluate(()=>document.documentElement.scrollWidth>innerWidth);if(overflow)throw new Error('Mobile overflow');
      await guest.screenshot({path:path.join(output,`share-mobile-dark-${name}.png`),fullPage:true});
      // A second link to the already uploaded object has an independent counter.
      await page.getByRole('button',{name:'创建分享链接',exact:true}).click();await linkInput.waitFor();
      await page.waitForFunction(previous=>document.querySelector('input[readonly]')?.value!==previous,shareUrl);
      const nativeUrl=await linkInput.inputValue();if(nativeUrl===shareUrl)throw new Error('Second link not independent');const verifyingNative=guest.waitForResponse(r=>r.url().endsWith('/api/public/shares/verify')&&r.status()===200);await guest.goto(nativeUrl);await verifyingNative;await guest.getByRole('heading',{name:`browser-${name}.txt`,exact:true}).waitFor();
      const nativeDownload=guest.waitForEvent('download');await guest.getByRole('button',{name:'流式保存到浏览器',exact:true}).click();const native=await nativeDownload;const nativePath=path.join(output,`browser-${name}-native.txt`);await native.saveAs(nativePath);
      if(crypto.createHash('sha256').update(await fs.readFile(nativePath)).digest('hex')!==expected)throw new Error('Native SHA mismatch');
      await guest.getByText('服务端完整交付已计一次',{exact:false}).waitFor();
      await page.getByRole('button',{name:'查询 / 刷新',exact:true}).click();
      const revoking=page.waitForResponse(r=>r.url().endsWith('/revoke')&&r.request().method()==='POST'&&r.status()===200);await page.locator('tr').filter({hasText:`browser-${name}.txt`}).first().getByRole('button',{name:'撤销链接',exact:true}).click();await revoking;await guest.getByRole('button',{name:'刷新名额',exact:true}).click();await guest.getByRole('alert').waitFor();
      if(name==='Chrome'){
        const mobileContext=await browser.newContext({...devices['Pixel 7'],acceptDownloads:true});const mobile=await mobileContext.newPage();await mobile.goto(shareUrl);await mobile.getByLabel('访问密码',{exact:true}).fill('browser-pass');await mobile.getByRole('button',{name:'验证访问密码'}).click();await mobile.getByRole('heading',{name:`browser-${name}.txt`,exact:true}).waitFor();
        const mobileDownload=mobile.waitForEvent('download');await mobile.getByRole('button',{name:'校验并下载',exact:true}).click();const md=await mobileDownload;const mp=path.join(output,'android-chrome-download.txt');await md.saveAs(mp);if(crypto.createHash('sha256').update(await fs.readFile(mp)).digest('hex')!==expected)throw new Error('Android simulation SHA');
        if(await mobile.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw new Error('Android simulation overflow');await mobile.screenshot({path:path.join(output,'share-android-chrome.png'),fullPage:true});await mobileContext.close();
      }
      const adminContext=await browser.newContext({viewport:{width:1440,height:1000}});const admin=await adminContext.newPage();admin.on('pageerror',e=>errors.push(e.message));await admin.goto(url+'/admin/');await admin.getByLabel('用户名',{exact:true}).fill('share_admin');await admin.getByLabel('密码',{exact:true}).fill('Synthetic-share-QA-2026');await admin.getByRole('button',{name:'登录',exact:true}).click();await admin.getByRole('button',{name:'分享管理',exact:true}).click();await admin.getByRole('heading',{name:'分享运行概览'}).waitFor();await admin.getByRole('button',{name:'刷新真实统计'}).click();await admin.screenshot({path:path.join(output,`share-admin-${name}.png`),fullPage:true});
      const adminRow=admin.locator('tr').filter({hasText:'ACTIVE'}).first();const adminRevoking=admin.waitForResponse(r=>r.url().includes('/api/admin/shares/')&&r.url().endsWith('/revoke')&&r.status()===200);await adminRow.getByRole('button',{name:'撤销链接',exact:true}).click();await adminRevoking;
      await admin.getByRole('button',{name:'系统总览',exact:true}).click();
      await admin.getByRole('heading',{name:'集成状态与验收边界',exact:true}).waitFor();
      await admin.getByText('没有正式授权',{exact:true}).waitFor();
      await admin.getByText('production：未验证',{exact:true}).waitFor();
      const validation = admin.getByRole('region', {name:'DeepSeek 验收记录'});
      await validation.getByText('NOT_CONFIGURED', {exact:true}).waitFor();
      if(!((await validation.innerText()).includes('PENDING')))throw new Error('Mock implied real AI acceptance');
      await admin.screenshot({path:path.join(output,`system-status-${name}.png`),fullPage:true});
      if(errors.length)throw new Error(errors.join('; '));
      results.push({browser:name,real_http:true,upload_via_ui:true,copy:true,edit:true,history:true,password_wrong_and_correct:true,verified_download_sha256:digest,native_download:true,multiple_links:true,revoke:true,admin_statistics:true,mobile_viewport:'390x844 desktop simulation',mobile_overflow:overflow,dark_theme:true,physical_mobile:false,js_errors:errors});
      await guests.close();await context.close();await adminContext.close();
    }catch(error){if(activeGuest){await activeGuest.screenshot({path:path.join(output,`share-guest-failure-${name}.png`),fullPage:true});await fs.writeFile(path.join(output,`share-guest-failure-${name}.txt`),await activeGuest.locator('body').innerText());}if(page){await page.screenshot({path:path.join(output,`share-browser-failure-${name}.png`),fullPage:true,mask:[page.locator('input[readonly]')]});await fs.writeFile(path.join(output,`share-browser-failure-${name}.txt`),(await page.locator('body').innerText()).replace(/https?:\/\/[^\s]*\/s\/#[A-Za-z0-9_-]+/g,'[REDACTED SHARE URL]'));}throw error;}finally{await browser.close();}
  }
  let webkitResult='not requested';
  if(process.env.FILEMIND_TEST_WEBKIT==='1'){
    const wb=await webkit.launch({headless:true});try{
      const owner=await wb.newContext();const login=await(await owner.request.post(url+'/api/auth/login',{data:{username:'share_qa',password:'Synthetic-share-QA-2026'}})).json();const headers={Authorization:'Bearer '+login.access_token};const files=await(await owner.request.get(url+'/api/library/search',{headers})).json();const file=await(await owner.request.get(url+'/api/library/'+files.items[0].file_id,{headers})).json();const version=file.versions.find(v=>v.version===1);
      const link=await(await owner.request.post(url+'/api/shares',{headers,data:{file_version_id:version.id,max_downloads:2,ttl_seconds:3600}})).json();
      const mobile=await wb.newContext({...devices['iPhone 13'],acceptDownloads:true,reducedMotion:'reduce'});const p=await mobile.newPage();await p.goto(url+link.share_url);await p.getByRole('heading',{name:version.filename,exact:true}).waitFor();const pending=p.waitForEvent('download');await p.getByRole('button',{name:'校验并下载',exact:true}).click();const download=await pending;const saved=path.join(output,'webkit-iphone-download.txt');await download.saveAs(saved);if(crypto.createHash('sha256').update(await fs.readFile(saved)).digest('hex')!==version.sha256)throw new Error('WebKit SHA');
      if(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw new Error('WebKit overflow');await p.getByRole('button',{name:'切换深色'}).click();await p.screenshot({path:path.join(output,'share-webkit-iphone-dark.png'),fullPage:true});webkitResult={engine:'WebKit on Windows',profile:'iPhone 13 emulation',hash_verified:true,responsive:true,dark_theme:true,real_iPhone:false};
      await mobile.close();await owner.close();
    }finally{await wb.close();}
  }
  const report={results,webkit:webkitResult,android_chrome_simulation:true,real_iOS:false,real_Android:false,production:false};await fs.writeFile(path.join(output,'browser-report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
})().catch(e=>{console.error(e);process.exitCode=1;});
