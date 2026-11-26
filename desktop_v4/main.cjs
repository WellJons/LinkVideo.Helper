'use strict';
const {app,BrowserWindow,ipcMain,dialog,clipboard,safeStorage,shell,nativeImage}=require('electron');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const {Bridge}=require('./bridge.cjs');
const {CredentialVault}=require('./credentials.cjs');
const {Updater,verifyInstaller}=require('./updater.cjs');
const {RuntimeLog}=require('./runtime-log.cjs');
const fsp=require('node:fs/promises');
const {version:appVersion}=require('./package.json');
app.setName('LinkVideo.Helper 4 Preview');
app.setPath('userData',process.argv.includes('--lv4-smoke')&&process.env.LV4_SMOKE==='1'?require('node:fs').mkdtempSync(path.join(require('node:os').tmpdir(),'lv4-smoke-')):path.join(app.getPath('appData'),'LinkVideo.Helper.v4-preview'));
if(!app.requestSingleInstanceLock()){app.quit()}else{
let win=null,approved=null,updateAbort=null,installBusy=false,lastArchive=null,createdB2O=null;
function clearB2OCopy(){if(createdB2O){createdB2O.login=null;createdB2O.password=null}createdB2O=null}
function expireRecord(record,clear,ms){const timer=setTimeout(()=>clear(record),ms);timer.unref()}
const updater=new Updater(appVersion);
const vault=new CredentialVault(app.getPath('userData'),safeStorage);
const runtimeLog=new RuntimeLog(path.join(app.getPath('userData'),'logs'));
const bridge=new Bridge({root:app.isPackaged?process.resourcesPath:path.resolve(__dirname,'..'),profile:app.getPath('userData'),command:app.isPackaged?path.join(process.resourcesPath,'worker','helper-core.exe'):null,onProgress:p=>{if(win&&!win.isDestroyed())win.webContents.send('helper:progress',p)}});
const smokeMode=process.argv.includes('--lv4-smoke')&&process.env.LV4_SMOKE==='1';
const home=pathToFileURL(path.join(__dirname,'index.html')).href;
function trusted(event){return win&&!win.isDestroyed()&&event.sender===win.webContents&&event.senderFrame===win.webContents.mainFrame&&event.senderFrame.url===home}
function clean(value,max){return typeof value==='string'&&value.length<=max?value:null}
function handle(channel,fn){ipcMain.handle(channel,async(event,arg)=>{
 if(!trusted(event))return {ok:false,error:'Запрос отклонён.'};
 const start=performance.now();let ok=false;
 try{const data=await fn(arg);ok=true;return {ok:true,data}}catch(e){return {ok:false,error:e.message||'Ошибка операции.'}}
 finally{
   if(!['helper:status','helper:remember-status','helper:copy','helper:log-read','helper:log-clear','helper:log-folder'].includes(channel)&&!(channel==='helper:operation'&&['traffic_sample','client_refresh'].includes(arg?.method))){
    const name=channel==='helper:operation'&&operations.has(arg?.method)?channel+':'+arg.method:channel;
    void runtimeLog.write(name,ok,performance.now()-start).catch(()=>{});
   }
 }
})}
handle('helper:log-read',()=>runtimeLog.recent());
handle('helper:log-clear',async()=>{const result=await dialog.showMessageBox(win,{type:'question',message:'Очистить локальный журнал Helper 4?',buttons:['Отмена','Очистить'],defaultId:0,cancelId:0});if(result.response!==1)throw Error('Очистка отменена.');return runtimeLog.clear()});
handle('helper:log-folder',async()=>{await fsp.mkdir(runtimeLog.dir,{recursive:true});const err=await shell.openPath(runtimeLog.dir);if(err)throw Error('Не удалось открыть папку журнала.');return true});
async function login(a){
 const username=clean(a?.username,128),password=clean(a?.password,512);
 if(!username?.trim()||!password)throw Error('Введите логин и пароль.');
 approved=null;clearB2OCopy();
 const result=await bridge.request('login',{username:username.trim(),password});
 let remembered=false;
 try{if(a.remember===true){vault.save('work',{username:username.trim(),password});remembered=true}else vault.clear('work')}catch{}
 return {...result,remembered};
}
handle('helper:login',login);
handle('helper:remember-status',()=>({work:vault.has('work'),admin:vault.has('admin'),cloud:vault.has('cloud'),available:vault.available()}));
handle('helper:login-saved',async()=>{const c=vault.load('work');if(!c)throw Error('Сохранённого рабочего входа нет.');try{return await login({...c,remember:true})}catch(e){vault.clear('work');throw e}});
handle('helper:clear-saved',a=>{if(!['work','admin','cloud'].includes(a?.kind))throw Error('Неизвестный аккаунт.');vault.clear(a.kind);return null});

handle('helper:status',()=>bridge.request('status'));
handle('helper:search',a=>{const query=clean(a?.query,128);if(!query?.trim()||!['login','port'].includes(a?.mode))throw Error('Некорректный запрос.');return bridge.request('search',{query:query.trim(),mode:a.mode})});
handle('helper:servers',()=>bridge.request('servers'));
handle('helper:analyze',a=>{const host=clean(a?.host,253);if(!host)throw Error('Выберите сервер.');return bridge.request('analyze',{host})});
const operations=new Set(['client_detail','client_refresh','traffic_sample','camera_ports','camera_lookup','camera_lookup_image','inactive_fleet','inactive_batch_preview','server_fleet','create_preview','vpn_preview','commit','inactive','lv_status','lv_preview','lv_fleet','lv_batch_preview','create_suggest','create_ip','create_check','admin_logout','admin_status','admin_retry','billing_refresh','billing_filter','billing_search','billing_select','billing_catalog','billing_history','billing_preview','billing_bulk_preview','billing_camera_image','create_auto','archive_discover','archive_settings','archive_settings_update','archive_diagnose','agent_search','agent_settings','agent_preview','templates_list','templates_save','templates_favorite','templates_delete','background_status','background_preview','background_verify_admin','backup','delete_preview','backup_status','backup_inventory','backup_verify','cloud_deleted_search','deleted_search','restore_preview','sheets_preview','cloud_status','cloud_restore_preview','activity','camera_inspect','camera_live','camera_image','agent_image','b2o_read','b2o_access','b2o_create_preview','billing_recipient','billing_transfer_preview','templates_images','templates_detach','winbox','registry_list','registry_update','background_status']);
handle('helper:operation',async a=>{
 if(!a||!operations.has(a.method)||!a.args||typeof a.args!=='object'||Array.isArray(a.args)||JSON.stringify(a.args).length>65000)throw Error('Недопустимая операция.');
 let confirmed=null;
 if(a.method==='b2o_create_preview')clearB2OCopy();
 if(a.method==='commit'){
  if(!approved||approved.token!==a.args.token||Date.now()>approved.deadline){approved=null;throw Error('Подтверждение устарело.');}
  confirmed=approved;approved=null;
  const answer=await dialog.showMessageBox(win,{type:'warning',buttons:['Отмена','Подтвердить'],defaultId:0,cancelId:0,title:'LinkVideo.Helper 4',message:confirmed.label,detail:'Проверьте выбранные данные. При неизвестном результате не повторяйте запрос автоматически.'});
  if(answer.response!==1)throw Error('Подтверждение отменено.');
 }
 if(a.method==='registry_update'&&a.args.action==='trust'){
 const target=clean(a.args.host,253);if(!target)throw Error('Некорректный сервер.');
 const answer=await dialog.showMessageBox(win,{type:'warning',message:'Разрешить подключение к '+target+'?',detail:'Рабочие данные MikroTik передаются этому серверу. Добавляйте только проверенные корпоративные адреса. Доверие действует текущий сеанс.',buttons:['Отмена','Разрешить'],defaultId:0,cancelId:0});if(answer.response!==1)throw Error('Доверие не подтверждено.');}
 const data=await bridge.request(a.method,a.args);
 if(a.method.endsWith('_preview')&&data?.token&&data?.label){
  const record={token:data.token,label:String(data.label).slice(0,600),deadline:Date.now()+120000};
  if(a.method==='b2o_create_preview')record.b2o={login:clean(a.args.login,100),password:clean(a.args.password,128)};
  approved=record;expireRecord(record,x=>{if(approved===x)approved=null;if(x.b2o){x.b2o.login=null;x.b2o.password=null}},120000);
 }
 if(confirmed?.b2o&&Number.isSafeInteger(data?.id_account)&&data.id_account>0){
  const record={id:data.id_account,login:confirmed.b2o.login,password:confirmed.b2o.password,deadline:Date.now()+300000};
  createdB2O=record;expireRecord(record,x=>{if(createdB2O===x)clearB2OCopy()},300000);
 }
 if(a.method==='admin_logout'||(a.method==='admin_retry'&&a.args.target==='b2o')){approved=null;clearB2OCopy();}
 return data;
});
handle('helper:copy-b2o-created',a=>{const c=createdB2O;if(!c||c.id!==a?.id||Date.now()>c.deadline||!c.login||!c.password)throw Error('Реквизиты недоступны или срок копирования истёк.');clipboard.writeText('Создали вам аккаунт:\nЛогин: '+c.login+'\nПароль: '+c.password+'\n\nДанные можете поменять после того, как авторизуетесь в личном кабинете.');return null});
async function adminLogin(a){
 const username=clean(a?.username,128),password=clean(a?.password,512),target=a?.target;
 if(!username?.trim()||!password||!['crm','b2o','both'].includes(target))throw Error('Проверьте данные админки.');
 approved=null;clearB2OCopy();
 const data=await bridge.request('admin_login',{username:username.trim(),password,target});
 let remembered=false;
 if(data.crm||data.b2o){try{if(a.remember===true){vault.save('admin',{username:username.trim(),password,target:data.crm&&data.b2o?'both':data.crm?'crm':'b2o'});remembered=true}else vault.clear('admin')}catch{}}
 return {...data,remembered};
}
handle('helper:admin-login',adminLogin);
handle('helper:admin-saved',async()=>{const c=vault.load('admin');if(!c)throw Error('Сохранённого входа админки нет.');return adminLogin({...c,remember:true})});
async function cloudLogin(a,saved=false){
 const host=clean(a?.host,253),username=clean(a?.username,128),password=clean(a?.password,512),port=a?.port,tls=a?.tls===true;
 if(!host||!/^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$/.test(host)||!username?.trim()||!password||!Number.isInteger(port)||port<1||port>65535)throw Error('Проверьте адрес и учётную запись VPNSync.');
 // Never connect to a user-entered plaintext endpoint without native consent.
 if(!tls){
  const answer=await dialog.showMessageBox(win,{type:'warning',title:'VPNSync · HTTP',message:'Подключиться к '+host+':'+port+' без HTTPS?',detail:'Логин, пароль и токен будут переданы без шифрования. Продолжайте только в доверенной корпоративной сети.',buttons:['Отмена','Подключить'],defaultId:0,cancelId:0});
  if(answer.response!==1)throw Error('Подключение отменено.');
 }
 const data=await bridge.request('cloud_connect',{host,port,username:username.trim(),password,tls,allow_http:!tls});
 let remembered=false;
 try{if(saved||a.remember===true){vault.save('cloud',{host,port,username:username.trim(),password,tls});remembered=true}else vault.clear('cloud')}catch{}
 return {...data,remembered};
}
handle('helper:cloud-login',a=>cloudLogin(a));
handle('helper:cloud-saved',async()=>{const c=vault.load('cloud');if(!c)throw Error('Сохранённого входа VPNSync нет.');return cloudLogin(c,true)});
handle('helper:cloud-profile',()=>{const c=vault.load('cloud');return c?{host:c.host,port:c.port,username:c.username,tls:c.tls,remembered:true}:null});
handle('helper:cloud-disconnect',async()=>{const result=await bridge.request('cloud_disconnect');vault.clear('cloud');return result});
handle('helper:task-scheduler',async()=>{
 if(process.platform!=='win32')throw Error('Поддерживается только Windows.');
 await bridge.request('background_status',{});
 const root=process.env.SystemRoot;
 if(!root||!path.isAbsolute(root))throw Error('Не найдена системная папка Windows.');
 const file=path.join(root,'System32','taskschd.msc');
 try{await fsp.access(file)}catch{throw Error('Планировщик Windows недоступен.')}
 const err=await shell.openPath(file);if(err)throw Error('Не удалось открыть планировщик.');
 return true;
});
let archiveFolder=null,archiveFolderLoaded=false;
const archiveFolderFile=path.join(app.getPath('userData'),'archive-folder.json');
async function loadArchiveFolder(){if(archiveFolderLoaded)return;archiveFolderLoaded=true;try{const data=JSON.parse(await fsp.readFile(archiveFolderFile,'utf8'));if(typeof data.folder==='string'&&path.isAbsolute(data.folder)&&(await fsp.stat(data.folder)).isDirectory())archiveFolder=data.folder}catch{}}
async function rememberArchiveFolder(folder){archiveFolder=folder;try{await fsp.mkdir(app.getPath('userData'),{recursive:true});await fsp.writeFile(archiveFolderFile,JSON.stringify({folder}),'utf8')}catch{}}
handle('helper:archive-folder',async a=>{
 if(!['get','choose'].includes(a?.action))throw Error('Неизвестное действие.');
 await loadArchiveFolder();
 if(a.action==='choose'){const r=await dialog.showOpenDialog(win,{title:'Папка архива',defaultPath:archiveFolder||app.getPath('videos'),properties:['openDirectory','createDirectory']});if(!r.canceled&&r.filePaths[0])await rememberArchiveFolder(r.filePaths[0])}
 return archiveFolder||app.getPath('videos');
});
handle('helper:archive-download',async a=>{
 if(!['ffmpeg','ffmpeg_no_audio','curl'].includes(a?.mode))throw Error('Выберите метод.');
 const manual=clean(a?.manual??'',4000);if(manual===null)throw Error('Слишком длинная команда FFmpeg.');
 const token=clean(a?.token,100);if(!token)throw Error('Повторите поиск архива.');
 const filename=typeof a.filename==='string'&&/^[A-Za-z0-9_.-]{1,180}\.mp4$/.test(a.filename)?a.filename:'archive.mp4';
 await loadArchiveFolder();
 const result=await dialog.showSaveDialog(win,{title:'Сохранить архив',defaultPath:path.join(archiveFolder||app.getPath('videos'),filename),filters:[{name:'Видео MP4',extensions:['mp4']}]});
 if(result.canceled||!result.filePath)throw Error('Сохранение отменено.');
 await rememberArchiveFolder(path.dirname(result.filePath));
 const out=await bridge.request('archive_download',{output:result.filePath,mode:a.mode,manual,token});lastArchive=out?.output===result.filePath?result.filePath:null;return out;
});
handle('helper:backup-folder',async()=>{
 const data=await bridge.request('backup_status',{});
 if(!data?.folder||!path.isAbsolute(data.folder))throw Error('Папка резервных копий недоступна.');
 await fsp.mkdir(data.folder,{recursive:true});
 const err=await shell.openPath(data.folder);if(err)throw Error('Не удалось открыть папку резервных копий.');
 return true;
});
handle('helper:archive-location',async()=>{
 if(!lastArchive)throw Error('Нет сохранённого архива текущего сеанса.');
 const stat=await fsp.stat(lastArchive);if(!stat.isFile())throw Error('Файл недоступен.');
 shell.showItemInFolder(lastArchive);return true;
});
handle('helper:template-copy',async a=>{
 if(!Number.isSafeInteger(a?.id)||a.id<1||a.image!=null&&(!Number.isSafeInteger(a.image)||a.image<1))throw Error('Выберите шаблон.');
 const reply=await bridge.request('templates_reply',{id:a.id,image:a.image??null});
 const text=typeof reply?.text==='string'?reply.text:'';
 const decoded=[];let skipped=0,total=0;
 for(const src of Array.isArray(reply?.images)?reply.images:[]){
  try{
   if(decoded.length>=12||typeof src!=='string'||src.length>7*1024*1024||!/^data:image\/(png|jpeg|webp|gif);base64,[A-Za-z0-9+/]+={0,2}$/i.test(src))throw Error('Unsupported image');
   const img=nativeImage.createFromDataURL(src);if(img.isEmpty())throw Error('Invalid image');
   const sz=img.getSize();if(sz.width<1||sz.height<1||sz.width>16000||sz.height>16000||sz.width*sz.height>24000000)throw Error('Image too large');
   const scaled=Math.max(sz.width,sz.height)>1600?img.resize(sz.width>=sz.height?{width:1600}:{height:1600}):img;
   if(scaled.isEmpty())throw Error('Invalid resize');
   const png=scaled.toPNG();if(!png.length||total+png.length>18*1024*1024)throw Error('Image budget');
   total+=png.length;decoded.push({image:scaled,src:'data:image/png;base64,'+png.toString('base64')});
  }catch{skipped++}
 }
 if(!decoded.length){clipboard.writeText(text);return {count:0,skipped:(reply.skipped||0)+skipped}}
 if(a.image!=null){clipboard.writeImage(decoded[0].image);return {count:1,skipped}}
 const escaped=text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
 const html='<html><body><div style="white-space:pre-wrap">'+escaped+'</div>'+decoded.map(x=>'<p><img src="'+x.src+'" style="max-width:100%;height:auto"></p>').join('')+'</body></html>';
 clipboard.write({text,html,image:decoded[0].image});
 return {count:decoded.length,skipped:(reply.skipped||0)+skipped};

});
handle('helper:copy-vpn-batch',async a=>{
 if(!Array.isArray(a?.targets)||a.targets.length<1||a.targets.length>20)throw Error('Нет созданных учёток.');
 const targets=a.targets.map(x=>({host:clean(x?.host,253),login:clean(x?.login,128)}));
 if(targets.some(x=>!x.host||!x.login))throw Error('Некорректные учётки.');
 const answer=await dialog.showMessageBox(win,{type:'warning',message:'Скопировать конфигурации '+targets.length+' VPN вместе с паролями?',detail:'Буфер обмена может быть доступен другим приложениям.',buttons:['Отмена','Копировать'],defaultId:0,cancelId:0});
 if(answer.response!==1)throw Error('Копирование отменено.');
 const value=await bridge.request('copy_vpn_batch',{targets});clipboard.writeText(value);return {count:targets.length};
});
handle('helper:template-paste',async a=>{
 if(!Number.isSafeInteger(a?.id)||a.id<1)throw Error('Сначала сохраните шаблон.');
 const pic=clipboard.readImage();if(pic.isEmpty())throw Error('В буфере обмена нет изображения.');
 const size=pic.getSize();if(size.width>12000||size.height>12000)throw Error('Слишком большое изображение.');
 const scaled=Math.max(size.width,size.height)>2500?pic.resize(size.width>=size.height?{width:2500}:{height:2500}):pic;
 const png=scaled.toPNG();if(png.length>20*1024*1024)throw Error('Изображение слишком велико.');
 const dir=await fsp.mkdtemp(path.join(app.getPath('temp'),'lv4-template-'));try{const file=path.join(dir,'clipboard.png');await fsp.writeFile(file,png,{mode:0o600});return await bridge.request('templates_attach',{id:a.id,path:file})}finally{await fsp.rm(dir,{recursive:true,force:true}).catch(()=>{})}
});
handle('helper:copy-vpn',async a=>{
 const host=clean(a?.host,253),login=clean(a?.login,128);
 if(!host||!login)throw Error('Выберите VPN-клиента.');
 const answer=await dialog.showMessageBox(win,{type:'warning',message:'Скопировать конфигурацию VPN вместе с паролем?',detail:'Буфер обмена может быть доступен другим приложениям.',buttons:['Отмена','Копировать'],defaultId:0,cancelId:0});
 if(answer.response!==1)throw Error('Копирование отменено.');
 const value=await bridge.request('copy_vpn',{host,login});clipboard.writeText(value);return null;
});
handle('helper:file-action',async a=>{
 const allowed={templates_import:{extensions:['docx'],name:'Шаблоны Word'},templates_migrate:{extensions:['sqlite3'],name:'Библиотека Helper 3.x'},templates_attach:{extensions:['png','jpg','jpeg','gif','webp'],name:'Изображение'},sheets_load:{extensions:['json'],name:'Google service account'}};
 if(!Object.hasOwn(allowed,a?.method))throw Error('Неизвестная операция.');
 const choice=await dialog.showOpenDialog(win,{title:a.method==='templates_migrate'?'Выберите старый templates.sqlite3 (папка support_templates)':'Выберите файл',properties:['openFile'],filters:[allowed[a.method]]});
 if(choice.canceled||!choice.filePaths[0])throw Error('Выбор отменён.');
 return bridge.request(a.method,{path:choice.filePaths[0],id:a?.id,category:clean(a?.category,70)||''});
});
handle('helper:update-check',()=>{if(installBusy)throw Error('Установка уже выполняется.');return updater.check()});
handle('helper:update-download',async()=>{
 if(bridge.pending||updater.active||installBusy)throw Error('Сначала завершите текущую операцию.');
 if(!updater.release)throw Error('Сначала проверьте обновления.');
 const r=await dialog.showSaveDialog(win,{title:'Сохранить проверенный установщик 4.0',defaultPath:updater.release.name,filters:[{name:'Установщик Windows',extensions:['exe']}]});
 if(r.canceled||!r.filePath)throw Error('Сохранение отменено.');
 updateAbort=new AbortController();
 try{return await updater.download(r.filePath,n=>{if(win&&!win.isDestroyed())win.webContents.send('helper:progress',{label:'Скачивание обновления 4.0',value:n})},updateAbort.signal)}finally{updateAbort=null}
});
handle('helper:update-install',async()=>{
 if(!app.isPackaged||process.platform!=='win32')throw Error('Установка доступна из Windows-сборки.');
 if(bridge.pending||updater.active||installBusy)throw Error('Сначала завершите текущую операцию.');
 if(!updater.release)throw Error('Сначала проверьте обновления.');
 const release={...updater.release};installBusy=true;
 let folder=null,launched=false;
 try{
  const answer=await dialog.showMessageBox(win,{type:'question',title:'Обновление LinkVideo.Helper 4',message:'Скачать и запустить '+release.version+'?',detail:'Проверяется SHA-256 из GitHub. Подпись издателя пока не настроена. Установщик будет запущен в обычном интерактивном режиме; приложение закроется. Версия 3.x не обновляется.',buttons:['Отмена','Загрузить и установить'],defaultId:0,cancelId:0});
  if(answer.response!==1)throw Error('Установка отменена.');
  if(updater.release?.version!==release.version||updater.release?.sha256!==release.sha256)throw Error('Версия изменилась. Повторите проверку.');
  const cache=path.join(app.getPath('userData'),'update-cache');await fsp.mkdir(cache,{recursive:true});folder=await fsp.mkdtemp(path.join(cache,'preview-'));
  const dest=path.join(folder,release.name);
  updateAbort=new AbortController();
  await updater.download(dest,n=>{if(win&&!win.isDestroyed())win.webContents.send('helper:progress',{label:'Скачивание и проверка обновления 4.0',value:n})},updateAbort.signal);
  updateAbort=null;
  await verifyInstaller(dest,release);
  if(bridge.pending)throw Error('Сначала завершите текущую рабочую операцию.');
  const result=await shell.openPath(dest);
  if(result)throw Error('Windows не запустила установщик.');
  launched=true;
  setTimeout(()=>app.quit(),600);
  return {version:release.version,launched:true};
 }finally{updateAbort=null;installBusy=false;if(folder&&!launched)await fsp.rm(folder,{recursive:true,force:true}).catch(()=>{})}
});
handle('helper:cancel',()=>{if(updateAbort){updateAbort.abort();return true}return bridge.cancel()});
handle('helper:copy',a=>{const value=clean(a?.text,50000);if(value===null)throw Error('Слишком длинный текст.');clipboard.writeText(value);return null});
handle('helper:logout',()=>{if(bridge.pending)throw Error('Сначала дождитесь завершения операции или отмените её.');approved=null;clearB2OCopy();lastArchive=null;bridge.stop();try{vault.clear('work');vault.clear('admin');vault.clear('cloud')}catch{}return null});
function open(){
 win=new BrowserWindow({width:1320,height:850,minWidth:820,minHeight:600,show:false,backgroundColor:'#171a20',title:'LinkVideo.Helper 4 — Preview',webPreferences:{preload:path.join(__dirname,'preload.cjs'),contextIsolation:true,sandbox:true,nodeIntegration:false,webSecurity:true,devTools:!app.isPackaged,additionalArguments:smokeMode?['--lv4-layout-fixtures']:[]}});
 win.webContents.setWindowOpenHandler(()=>({action:'deny'}));
 win.webContents.on('will-navigate',e=>e.preventDefault());
 win.webContents.on('will-attach-webview',e=>e.preventDefault());
 win.webContents.session.setPermissionRequestHandler((_wc,_permission,callback)=>callback(false));
 win.webContents.session.setPermissionCheckHandler(()=>false);
 win.once('ready-to-show',()=>win.show());
 win.on('close',e=>{if((bridge.pending||updater.active||installBusy)&&!smokeMode){e.preventDefault();if(updateAbort)updateAbort.abort();else bridge.cancel();void dialog.showMessageBox(win,{type:'info',message:'Запрошена отмена. Дождитесь окончания операции перед закрытием.'})}});
 win.on('closed',()=>{bridge.stop();win=null});
 if(smokeMode)win.webContents.once('did-finish-load',()=>{smoke().then(()=>{console.log('Electron UI smoke passed');app.exit(0)},err=>{console.error(err.message);app.exit(1)})});
 win.loadFile(path.join(__dirname,'index.html'));
}

async function smoke(){
 const cold=performance.now();await bridge.request('status');const coldMs=performance.now()-cold;
 const timings=[];for(let i=0;i<15;i++){const started=performance.now();await bridge.request('status');timings.push(performance.now()-started)}
 const attempt=await win.webContents.executeJavaScript(`(async()=>{const wait=()=>new Promise(r=>setTimeout(r,300));const get=id=>document.getElementById(id);if(!window.helper||get('login').hidden)throw Error('Missing login');const core=await window.helper.status();if(!core.ok||core.data.authenticated||core.data.version!=='4.0.0-alpha.13')throw Error('Worker not reachable');get('preview').click();await wait();if(get('workspace').hidden||!get('page').innerText.includes('Демонстрационный режим'))throw Error('Preview missing');const nav=id=>get('nav').querySelector('button[data-page="'+id+'"]');const expected=['create','search','archives','diagnostics','billing','agent','templates','servers','inactive','settings'];const expectedLabels=['Создание клиента','Поиск и управление','Скачать архив','Диагностика архива','Клиенты и тарифы','Новый Агент','Шаблоны ответов','VPN-серверы','VPN-клиенты','Настройки'];if(page!=='create'||JSON.stringify([...get('nav').querySelectorAll('button')].map(b=>b.dataset.page))!==JSON.stringify(expected)||JSON.stringify([...get('nav').querySelectorAll('button span')].map(b=>b.textContent))!==JSON.stringify(expectedLabels))throw Error('Desktop navigation or startup differs from 3.0.27');nav('search').click();await wait();if(!get('page').querySelector('button[type=submit]').disabled)throw Error('Preview search active');nav('settings').click();await wait();const sw=get('page').querySelector('[role=switch]');const before=sw.getAttribute('aria-checked');sw.click();if(get('page').querySelector('[role=switch]').getAttribute('aria-checked')===before)throw Error('Motion switch failed');for(const id of expected){nav(id).click();await wait();if(!get('page').querySelector('h1'))throw Error('Missing view: '+id)}for(const [child,parent] of Object.entries(secondaryRoutes)){nav(parent).click();await wait();const tab=get('page').querySelector('[data-section-route="'+child+'"]');if(!tab)throw Error('Missing child: '+child);tab.click();await wait();if(page!==child||nav(parent).getAttribute('aria-current')!=='page'||!get('page').querySelector('h1'))throw Error('Wrong nested route: '+child);get('page').querySelector('[data-section-route="'+parent+'"]').click();await wait();if(page!==parent)throw Error('Cannot return to parent: '+parent)}nav('settings').click();get('theme').click();await wait();if(!document.documentElement.classList.contains('light'))throw Error('Theme failed');get('logout').click();await wait();if(get('login').hidden||!get('workspace').hidden)throw Error('Logout failed');enter({username:'Offline employee UI test',role:'employee',writes:false});await wait();if(nav('servers')||nav('inactive')||nav('lv')||nav('recovery'))throw Error('Employee infrastructure visible');if(!nav('search')||!nav('create'))throw Error('Employee support inaccessible');const old=page;for(const id of ['inactive','servers','lv','recovery']){go(id);if(page!==old)throw Error('Restricted route accepted: '+id)}window.lvModules.reset();identity=null;get('workspace').hidden=true;get('login').hidden=false;const gate=await window.helper.status();if(!gate.ok||gate.data.authenticated)throw Error('UI fixture changed worker auth');return true})()`);
 if(!attempt)throw Error('Smoke test failed');
 const {layoutSmoke}=require('./layout-smoke.cjs');const layoutResults=[];
 const reportDir=process.env.LV4_METRICS_PATH?path.join(path.dirname(process.env.LV4_METRICS_PATH),'layout'):null;
 if(reportDir)await fsp.mkdir(reportDir,{recursive:true});
 for(const [width,height,zoom] of [[820,600,1],[1024,768,1.25],[1440,900,1]]){
  win.setSize(width,height);win.webContents.setZoomFactor(zoom);
  const result=await win.webContents.executeJavaScript('('+layoutSmoke.toString()+')()');
  layoutResults.push({width,height,zoom,...result});
  if(reportDir){const screenshot=await win.webContents.capturePage();await fsp.writeFile(path.join(reportDir,'billing-'+width+'-'+zoom+'.png'),screenshot.toPNG())}
 }
 win.webContents.setZoomFactor(1);
 if(reportDir)await fsp.writeFile(path.join(reportDir,'checks.json'),JSON.stringify(layoutResults,null,2));
 console.log('LV4_LAYOUT '+JSON.stringify(layoutResults.map(r=>({width:r.width,height:r.height,zoom:r.zoom,checks:r.checks.length}))));
 const metrics={coldCoreMs:Math.round(coldMs),warmRpcMeanMs:Math.round(timings.reduce((a,b)=>a+b,0)/timings.length*100)/100,electronProcesses:app.getAppMetrics().map(x=>({type:x.type,workingSetKB:x.memory?.workingSetSize||0}))};
 console.log('LV4_METRICS '+JSON.stringify(metrics));if(process.env.LV4_METRICS_PATH)require('node:fs').writeFileSync(process.env.LV4_METRICS_PATH,JSON.stringify(metrics,null,2));
}

app.whenReady().then(open);
if(smokeMode)setTimeout(()=>app.exit(1),150000).unref();
app.on('second-instance',()=>{if(win){if(win.isMinimized())win.restore();win.focus()}});
app.on('before-quit',()=>bridge.stop());
app.on('window-all-closed',()=>app.quit());
}
