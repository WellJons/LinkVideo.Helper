'use strict';
const fs=require('node:fs/promises'),path=require('node:path');
class RuntimeLog{
 constructor(dir){this.dir=dir;this.file=path.join(dir,'operations.jsonl');this.queue=Promise.resolve()}
 serial(fn){const next=this.queue.then(fn);this.queue=next.catch(()=>{});return next}
 write(action,ok,ms){
  // Only validated operation identifiers and durations. No parameters, result,
  // exception text, identities, clipboard contents, URLs or credentials.
  if(typeof action!=='string'||!/^helper:[a-z-]+(?::[a-z_]+)?$/.test(action)||action.length>90)return Promise.resolve();
  const row=JSON.stringify({time:new Date().toISOString(),action,ok:ok===true,ms:Math.max(0,Math.round(Number(ms)||0))})+'\n';
  return this.serial(async()=>{await fs.mkdir(this.dir,{recursive:true});let n=0;try{n=(await fs.stat(this.file)).size}catch(e){if(e.code!=='ENOENT')throw e}if(n>512*1024){await fs.rm(this.file+'.previous',{force:true});await fs.rename(this.file,this.file+'.previous')}await fs.appendFile(this.file,row,{mode:0o600})})
 }
 recent(){return this.serial(async()=>{const entries=[];for(const file of [this.file+'.previous',this.file]){try{const stat=await fs.stat(file);if(stat.size>1024*1024)continue;for(const row of (await fs.readFile(file,'utf8')).split('\n')){try{const x=JSON.parse(row);if(typeof x.action==='string'&&/^helper:[a-z-]+(?::[a-z_]+)?$/.test(x.action)&&typeof x.time==='string'&&typeof x.ok==='boolean'&&Number.isSafeInteger(x.ms))entries.push({time:x.time,action:x.action,ok:x.ok,ms:x.ms})}catch{}}}catch(e){if(e.code!=='ENOENT')throw e}}return entries.slice(-1000)})}
 clear(){return this.serial(async()=>{for(const f of [this.file,this.file+'.previous'])await fs.rm(f,{force:true});return true})}
}
module.exports={RuntimeLog};
