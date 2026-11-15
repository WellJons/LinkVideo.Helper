'use strict';
const {spawn}=require('node:child_process');
const {createInterface}=require('node:readline');
const path=require('node:path');
class Bridge {
 constructor({root=path.resolve(__dirname,'..'),python=process.env.LV_HELPER_PYTHON||'python',spawnFn=spawn,timeout=90000,command=null,profile=null,onProgress=()=>{}}={}){
  this.command=command;this.profile=profile;this.onProgress=onProgress;this.root=root;this.python=python;this.spawnFn=spawnFn;this.timeout=timeout;this.next=0;this.pending=null;this.child=null;
 }
 start(){
  if(this.child)return;
  const child=this.spawnFn(this.command||this.python,this.command?[]:['-u','-m','desktop_v4.worker'],{cwd:this.root,windowsHide:true,stdio:['pipe','pipe','ignore'],env:{...process.env,PYTHONIOENCODING:'utf-8',...(this.profile?{LV4_DATA_DIR:this.profile}:{})}});
  this.child=child;
  const reader=createInterface({input:child.stdout,crlfDelay:Infinity});
  reader.on('line',line=>{if(this.child!==child)return;let data;try{data=JSON.parse(line)}catch{return this.stop('Некорректный ответ Python-ядра.')}if(this.pending&&data.id===this.pending.id&&data.event==='progress'){this.onProgress({label:data.label,value:data.value});return}if(!this.pending||data.id!==this.pending.id||typeof data.ok!=='boolean')return this.stop('Нарушен протокол ядра.');const p=this.pending;this.pending=null;clearTimeout(p.timer);if(data.ok)p.resolve(data.result);else p.reject(new Error(typeof data.error==='string'?data.error:'Ошибка операции.'));});
  child.on('error',()=>{if(this.child===child)this.stop('Не удалось запустить Python. Проверьте установку и зависимости.');});
  child.on('exit',()=>{if(this.child===child)this.stop('Python-ядро завершилось. Войдите снова.');});
  child.stdin.on('error',()=>{if(this.child===child)this.stop('Связь с ядром прервана. Войдите снова.');});
 }
 request(method,args={}){
  if(this.pending)return Promise.reject(new Error('Дождитесь завершения текущей операции.'));
  try{this.start()}catch{return Promise.reject(new Error('Не удалось запустить Python-ядро.'))}
  return new Promise((resolve,reject)=>{
   const id=++this.next;
   const timer=setTimeout(()=>this.stop('Ядро не ответило. Войдите снова.'),['archive_discover','archive_diagnose','archive_download','commit','backup','lv_fleet','inactive_fleet','camera_ports','server_fleet','inactive_batch_preview','lv_batch_preview','delete_preview','backup_verify','background_verify_admin'].includes(method)?Math.max(this.timeout,7200000):this.timeout);
   this.pending={id,timer,resolve,reject};
   try{this.child.stdin.write(JSON.stringify({id,method,args})+'\n')}catch{this.stop('Связь с ядром прервана.')}
  });
 }
 cancel(){if(!this.pending||!this.child)return false;try{this.child.stdin.write(JSON.stringify({id:this.pending.id,method:'cancel'})+'\n');return true}catch{return false}}
 stop(message='Сессия завершена.'){
  const child=this.child;this.child=null;
  if(this.pending){const p=this.pending;this.pending=null;clearTimeout(p.timer);p.reject(new Error(message))}
  if(child){child.stdin.destroy();child.kill()}
 }
}
module.exports={Bridge};
