'use strict';
const {app,BrowserWindow,ipcMain}=require('electron');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const {Bridge}=require('./bridge.cjs');
app.setName('LinkVideo.Helper 4 Preview');
app.setPath('userData',path.join(app.getPath('appData'),'LinkVideo.Helper.v4-preview'));
if(!app.requestSingleInstanceLock()){app.quit()}else{
const bridge=new Bridge();let win=null;
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
 win.loadFile('index.html');
}
app.whenReady().then(open);
app.on('second-instance',()=>{if(win){if(win.isMinimized())win.restore();win.focus()}});
app.on('before-quit',()=>bridge.stop());
app.on('window-all-closed',()=>app.quit());
}
