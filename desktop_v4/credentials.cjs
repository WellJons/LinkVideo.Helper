'use strict';
const fs=require('node:fs');
const path=require('node:path');
class CredentialVault{
 constructor(dir,safeStorage,platform=process.platform){this.dir=dir;this.safeStorage=safeStorage;this.platform=platform}
 file(kind){if(!['work','admin'].includes(kind))throw Error('Unknown vault');return path.join(this.dir,'credentials-'+kind+'.bin')}
 available(){return this.platform==='win32'&&this.safeStorage.isEncryptionAvailable()}
 save(kind,credentials){
  if(!this.available())throw Error('Шифрование Windows недоступно.');
  if(typeof credentials.username!=='string'||typeof credentials.password!=='string'||!credentials.username||!credentials.password)throw Error('Invalid credentials');
  fs.mkdirSync(this.dir,{recursive:true});
  const p=this.file(kind),temp=p+'.tmp';
  try{fs.writeFileSync(temp,this.safeStorage.encryptString(JSON.stringify(credentials)),{mode:0o600});fs.renameSync(temp,p)}finally{fs.rmSync(temp,{force:true})}
 }
 load(kind){
  if(!this.available())return null;
  try{const p=this.file(kind);if(fs.statSync(p).size>16384)return null;const v=JSON.parse(this.safeStorage.decryptString(fs.readFileSync(p)));return typeof v.username==='string'&&typeof v.password==='string'&&v.username&&v.password?v:null}catch{return null}
 }
 has(kind){return !!this.load(kind)}
 clear(kind){fs.rmSync(this.file(kind),{force:true})}
}
module.exports={CredentialVault};
