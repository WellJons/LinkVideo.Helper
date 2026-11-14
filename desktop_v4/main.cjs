'use strict';
const {app,BrowserWindow,ipcMain}=require('electron');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const {Bridge}=require('./bridge.cjs');
app.setName('LinkVideo.Helper 4 Preview');
app.setPath('userData',process.argv.includes('--lv4-smoke')&&process.env.LV4_SMOKE==='1'?require('node:fs').mkdtempSync(path.join(require('node:os').tmpdir(),'lv4-smoke-')):path.join(app.getPath('appData'),'LinkVideo.Helper.v4-preview'));
if(!app.requestSingleInstanceLock()){app.quit()}else{
const bridge=new Bridge();let win=null;
const smokeMode=process.argv.includes('--lv4-smoke')&&process.env.LV4_SMOKE==='1';
const home=pathToFileURL(path.join(__dirname,'index.html')).href;
function trusted(event){return win&&!win.isDestroyed()&&event.sender===win.webContents&&event.senderFrame===win.webContents.mainFrame&&event.senderFrame.url===home}
function clean(value,max){return typeof value==='string'&&value.length<=max?value:null}
function handle(channel,fn){ipcMain.handle(channel,async(event,arg)=>{if(!trusted(event))return {ok:false,error:'Запрос отклонён.'};try{return {ok:true,data:await fn(arg)}}catch(e){return {ok:false,error:e.message||'Ошибка операции.'}}})}
handle('helper:login',a=>{const username=clean(a?.username,128),password=clean(a?.password,512);if(!username?.trim()||!password)throw Error('Введите логин и пароль.');return bridge.request('login',{username:username.trim(),password})});
handle('helper:status',()=>bridge.request('status'));
handle('helper:search',a=>{const query=clean(a?.query,128);if(!query?.trim()||!['login','port'].includes(a?.mode))throw Error('Некорректный запрос.');return bridge.request('search',{query:query.trim(),mode:a.mode})});
handle('helper:servers',()=>bridge.request('servers'));
handle('helper:analyze',a=>{const host=clean(a?.host,253);if(!host)throw Error('Выберите сервер.');return bridge.request('analyze',{host})});
handle('helper:logout',()=>{bridge.stop();return null});
function open(){
 win=new BrowserWindow({width:1320,height:850,minWidth:820,minHeight:600,show:false,backgroundColor:'#171a20',title:'LinkVideo.Helper 4 — Preview',webPreferences:{preload:path.join(__dirname,'preload.cjs'),contextIsolation:true,sandbox:true,nodeIntegration:false,webSecurity:true,devTools:!app.isPackaged}});
 win.webContents.setWindowOpenHandler(()=>({action:'deny'}));
 win.webContents.on('will-navigate',e=>e.preventDefault());
 win.webContents.on('will-attach-webview',e=>e.preventDefault());
 win.webContents.session.setPermissionRequestHandler((_wc,_permission,callback)=>callback(false));
 win.webContents.session.setPermissionCheckHandler(()=>false);
 win.once('ready-to-show',()=>win.show());
 win.on('closed',()=>{bridge.stop();win=null});
 if(smokeMode)win.webContents.once('did-finish-load',()=>{smoke().then(()=>{console.log('Electron UI smoke passed');app.exit(0)},err=>{console.error(err.message);app.exit(1)})});
 win.loadFile('index.html');
}

async function smoke(){
 const attempt=await win.webContents.executeJavaScript(`(async()=>{const wait=()=>new Promise(r=>setTimeout(r,300));const get=id=>document.getElementById(id);if(!window.helper||get('login').hidden)throw Error('Missing login');get('preview').click();await wait();if(get('workspace').hidden||!get('page').innerText.includes('Демонстрационный режим'))throw Error('Preview missing');const nav=label=>[...get('nav').querySelectorAll('button')].find(b=>b.innerText.includes(label));nav('Поиск клиентов').click();await wait();if(!get('page').querySelector('button[type=submit]').disabled)throw Error('Preview search active');nav('Настройки').click();await wait();const sw=get('page').querySelector('[role=switch]');const before=sw.getAttribute('aria-checked');sw.click();if(get('page').querySelector('[role=switch]').getAttribute('aria-checked')===before)throw Error('Motion switch failed');get('theme').click();await wait();if(!document.documentElement.classList.contains('light'))throw Error('Theme failed');get('logout').click();await wait();if(get('login').hidden||!get('workspace').hidden)throw Error('Logout failed');return true})()`);
 if(!attempt)throw Error('Smoke test failed');
}

app.whenReady().then(open);
if(smokeMode)setTimeout(()=>app.exit(1),30000).unref();
app.on('second-instance',()=>{if(win){if(win.isMinimized())win.restore();win.focus()}});
app.on('before-quit',()=>bridge.stop());
app.on('window-all-closed',()=>app.quit());
}
