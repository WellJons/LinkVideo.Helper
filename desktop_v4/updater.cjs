'use strict';
const fs=require('node:fs'),fsp=require('node:fs/promises'),path=require('node:path'),crypto=require('node:crypto');
const {Readable,Transform}=require('node:stream');const {pipeline}=require('node:stream/promises');
const REPO='WellJons/LinkVideo.Helper',API='https://api.github.com/repos/'+REPO+'/releases?per_page=100',LIMIT=700*1024*1024;
function version(s){const m=/^4\.(0|[1-9]\d{0,4})\.(0|[1-9]\d{0,4})-(alpha|beta|rc)\.(0|[1-9]\d{0,5})$/.exec(s);return m?[4,+m[1],+m[2],{alpha:0,beta:1,rc:2}[m[3]],+m[4]]:null}
function compare(a,b){const x=version(a),y=version(b);if(!x||!y)throw Error('Unsupported preview version');for(let i=0;i<x.length;i++)if(x[i]!==y[i])return Math.sign(x[i]-y[i]);return 0}
function selectRelease(rows,current){
 if(!version(current)||!Array.isArray(rows))throw Error('Некорректный ответ канала обновлений.');
 const candidates=[];
 for(const row of rows){if(!row||typeof row.tag_name!=='string')continue;const v=row.tag_name.slice(1);
  if(!version(v)||row.tag_name!=='v'+v||row.draft!==false||row.prerelease!==true||compare(v,current)<=0||!Array.isArray(row.assets))continue;
  const name='LinkVideo.Helper.'+v+'-Setup.exe',url='https://github.com/'+REPO+'/releases/download/v'+v+'/'+name;
  const assets=row.assets.filter(a=>a&&a.name===name&&a.state==='uploaded'&&a.browser_download_url===url&&Number.isSafeInteger(a.size)&&a.size>0&&a.size<=LIMIT&&typeof a.digest==='string'&&/^sha256:[a-f0-9]{64}$/i.test(a.digest));
  if(assets.length===1)candidates.push({version:v,name,url,size:assets[0].size,sha256:assets[0].digest.slice(7).toLowerCase(),notes:String(row.body||'').slice(0,6000)});
 }return candidates.sort((a,b)=>compare(b.version,a.version))[0]||null;
}
function approvedRedirect(url){try{const u=new URL(url);return u.protocol==='https:'&&!u.username&&!u.password&&!u.port&&u.hostname==='release-assets.githubusercontent.com'}catch{return false}}
async function verifyInstaller(filename,release){
 const stat=await fsp.lstat(filename);
 if(!stat.isFile()||stat.isSymbolicLink()||stat.size!==release.size)throw Error('Размер файла не совпадает.');
 const hash=crypto.createHash('sha256');let bytes=0;
 for await(const data of fs.createReadStream(filename)){bytes+=data.length;if(bytes>release.size)throw Error('Размер файла не совпадает.');hash.update(data)}
 if(bytes!==release.size||hash.digest('hex')!==release.sha256)throw Error('SHA-256 установщика не совпадает.');
 return true;
}
class Updater{
 constructor(current,{fetchFn=fetch}={}){this.current=current;this.fetch=fetchFn;this.release=null;this.active=false}
 async check(){
  if(this.active)throw Error('Обновление уже скачивается.');this.release=null;
  const response=await this.fetch(API,{headers:{Accept:'application/vnd.github+json','User-Agent':'LinkVideo.Helper/4','X-GitHub-Api-Version':'2022-11-28'},redirect:'error',signal:AbortSignal.timeout(15000)});
  if(!response.ok)throw Error('Канал обновлений недоступен (HTTP '+response.status+').');
  this.release=selectRelease(await response.json(),this.current);const x=this.release;
  return {current:this.current,available:!!x,release:x?{version:x.version,name:x.name,size:x.size,notes:x.notes}:null}
 }
 async download(dest,onProgress=()=>{},signal){
  if(this.active)throw Error('Обновление уже скачивается.');const rel=this.release;if(!rel)throw Error('Сначала проверьте обновления.');
  if(!path.isAbsolute(dest)||path.extname(dest).toLowerCase()!=='.exe')throw Error('Выберите новый EXE-файл.');
  const abort=AbortSignal.any([AbortSignal.timeout(30*60*1000),...(signal?[signal]:[])]);this.active=true;let staging;
  try{
   try{await fsp.access(dest);throw Error('Файл уже существует. Выберите новый путь.')}catch(e){if(e.code!=='ENOENT')throw e}
   staging=await fsp.mkdtemp(path.join(path.dirname(dest),'.lv4-update-'));
   let response=await this.fetch(rel.url,{redirect:'manual',signal:abort});
   if([301,302,303,307,308].includes(response.status)){const location=response.headers.get('location');if(!approvedRedirect(location))throw Error('Недоверенный адрес загрузки.');await response.body?.cancel();response=await this.fetch(location,{redirect:'error',signal:abort})}
   if(!response.ok||!response.body)throw Error('Не удалось скачать обновление.');
   const declared=response.headers.get('content-length');if(declared&&Number(declared)!==rel.size)throw Error('Размер обновления не совпадает.');
   const hash=crypto.createHash('sha256');let received=0,last=-1;const file=path.join(staging,'update.exe');
   const meter=new Transform({transform(chunk,_enc,cb){received+=chunk.length;if(received>rel.size)return cb(Error('Размер обновления превышен.'));hash.update(chunk);const n=Math.floor(100*received/rel.size);if(n!==last){last=n;onProgress(n)}cb(null,chunk)}});
   await pipeline(Readable.fromWeb(response.body),meter,fs.createWriteStream(file,{flags:'wx'}),{signal:abort});
   if(received!==rel.size||hash.digest('hex')!==rel.sha256)throw Error('SHA-256 или размер не совпадает. Файл удалён.');
   await fsp.copyFile(file,dest,fs.constants.COPYFILE_EXCL);return {path:dest,version:rel.version,verified:true};
  }finally{this.active=false;if(staging)await fsp.rm(staging,{recursive:true,force:true}).catch(()=>{})}
 }
}
module.exports={Updater,selectRelease,compare,approvedRedirect,verifyInstaller};
