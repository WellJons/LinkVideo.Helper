'use strict';
const path=require('node:path');
const fs=require('node:fs');
const {packager}=require('@electron/packager');
(async()=>{
 const root=path.resolve(__dirname,'..');
 const worker=path.join(root,'dist-v4-core','worker');
 if(!fs.existsSync(path.join(worker,'helper-core.exe')))throw Error('Python worker missing. Build it first.');
 const result=await packager({dir:__dirname,name:'LinkVideo.Helper.4',platform:'win32',arch:'x64',out:path.join(root,'dist-v4'),overwrite:true,asar:true,prune:true,icon:path.join(root,'icon.ico'),extraResource:[worker],ignore:[/^\/test(?:\/|$)/,/^\/__pycache__(?:\/|$)/,/^\/[^/]+\.(?:py|ps1|iss|md|txt)$/i,/^\/package-win\.cjs$/,/^\/\.gitignore$/]});
 // Only the Russian interface and English fallback are supported.
 for(const folder of result){
   const locales=path.join(folder,'locales');if(!fs.existsSync(locales))continue;
   const keep=new Set(['ru.pak','en-US.pak','en-GB.pak']);let removed=0;
   for(const item of fs.readdirSync(locales)){
     if(item.toLowerCase().endsWith('.pak')&&!keep.has(item)){fs.rmSync(path.join(locales,item),{force:true});removed++}
   }
   console.log('Unused Chromium locale packs removed: '+removed);
 }
 console.log(result.join('\n'));
})().catch(e=>{console.error(e);process.exitCode=1});
