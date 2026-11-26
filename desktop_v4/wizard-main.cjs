'use strict';
const {app,BrowserWindow,ipcMain}=require('electron');
const path=require('node:path');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {pathToFileURL}=require('node:url');
function start(){
 const mode=process.argv.find(x=>x.startsWith('--lv4-wizard='))?.split('=')[1];
 const preview=process.argv.includes('--lv4-wizard-preview')&&process.env.LV4_SMOKE==='1';
 if(!['install','uninstall'].includes(mode)||(!app.isPackaged&&!preview)){app.exit(1);return}
 const profile=process.argv.find(x=>x.startsWith('--lv4-wizard-profile='))?.slice('--lv4-wizard-profile='.length);
 if(!profile||!path.isAbsolute(profile)){app.exit(1);return}
 app.setPath('userData',profile);
 const target=path.join(process.env.LOCALAPPDATA||'', 'Programs','LinkVideo.Helper.4');
 const helper=path.join(path.dirname(process.execPath),'Uninstall.exe');
 const home=pathToFileURL(path.join(__dirname,'wizard.html')).href;
 let win,busy=false,finished=false;
 function trusted(e){return win&&!win.isDestroyed()&&e.sender===win.webContents&&e.senderFrame===win.webContents.mainFrame&&e.senderFrame.url===home}
 function handle(channel,fn){ipcMain.handle(channel,async(e,a)=>{if(!trusted(e))return {ok:false,error:'Запрос отклонён'};try{return {ok:true,data:await fn(a)}}catch(err){return {ok:false,error:err.message}}})}
 function invoke(action,flag){
  return new Promise((resolve,reject)=>{
   const child=spawn(helper,['--wizard-operation',action,flag?'yes':'no'],{windowsHide:true,stdio:['ignore','pipe','pipe']});
   let buffer='',error='';
   child.stdout.setEncoding('utf8');child.stdout.on('data',s=>{buffer+=s;let i;while((i=buffer.indexOf('\n'))>=0){const line=buffer.slice(0,i);buffer=buffer.slice(i+1);try{const p=JSON.parse(line);if(p.percent<0)error=String(p.label);else if(Number.isFinite(p.percent)&&win&&!win.isDestroyed())win.webContents.send('wizard:progress',{percent:Math.min(100,Math.max(0,p.percent)),label:String(p.label).slice(0,500)})}catch{}}});
   child.stderr.resume();child.on('error',reject);child.on('close',code=>code===0?resolve():reject(Error(error||'Операция завершилась с ошибкой ('+code+').')));
  })
 }
 handle('wizard:info',async()=>({mode,version:require('./package.json').version,target,upgrade:mode==='install'&&await fs.stat(path.join(target,'LinkVideo.Helper.4.exe')).then(()=>true,()=>false),preview}));
 handle('wizard:run',async a=>{if(preview||busy||finished||typeof a?.option!=='boolean')throw Error('Операция недоступна');busy=true;try{await invoke(mode,a.option);finished=true;return true}finally{busy=false}});
 handle('wizard:close',async a=>{if(busy)throw Error('Дождитесь завершения операции');if(a?.launch===true&&finished&&mode==='install'&&!preview)await invoke('launch',false);app.quit();return true});
 app.whenReady().then(async()=>{
  win=new BrowserWindow({width:920,height:620,minWidth:690,minHeight:470,show:false,backgroundColor:'#fff',title:mode==='install'?'Установка LinkVideo.Helper 4':'Удаление LinkVideo.Helper 4',autoHideMenuBar:true,webPreferences:{preload:path.join(__dirname,'wizard-preload.cjs'),contextIsolation:true,sandbox:true,nodeIntegration:false,webSecurity:true,devTools:false}});
  win.webContents.setWindowOpenHandler(()=>({action:'deny'}));win.webContents.on('will-navigate',e=>e.preventDefault());
  win.on('close',e=>{if(busy)e.preventDefault()});
  await win.loadFile('wizard.html');win.show();
  if(preview){
   const out=process.env.LV4_WIZARD_REPORT;if(!out){app.exit(1);return}
   try{
    await fs.mkdir(out,{recursive:true});
    await win.webContents.executeJavaScript('new Promise((resolve,reject)=>{let i=0;const t=setInterval(()=>{if(window.wizardFixture){clearInterval(t);resolve(true)}else if(++i>100){clearInterval(t);reject(Error("Fixture unavailable"))}},50)})');
    for(const [w,h,zoom] of [[920,620,1],[920,620,1.25],[1024,768,1.5],[690,470,1]]){
     win.setSize(w,h);win.webContents.setZoomFactor(zoom);await new Promise(r=>setTimeout(r,150));
     for(const page of mode==='install'?['welcome','options','progress','finish','error']:['welcome','progress','finish','error']){
      const check=await win.webContents.executeJavaScript('window.wizardFixture('+JSON.stringify(page)+')');
      if(!check)throw Error('Wizard layout '+page+' '+w+' '+zoom);
      await new Promise(r=>setTimeout(r,220));
      await fs.writeFile(path.join(out,mode+'-'+page+'-'+w+'-'+zoom+'.png'),(await win.webContents.capturePage()).toPNG())
     }
    }
    app.exit(0)
   }catch(e){console.error(e);app.exit(1)}
  }
 }).catch(e=>{console.error(e);app.exit(1)});
 app.on('window-all-closed',()=>app.quit());
}
module.exports={start};
