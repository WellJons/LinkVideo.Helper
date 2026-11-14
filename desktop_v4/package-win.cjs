'use strict';
const path=require('node:path');
const fs=require('node:fs');
const {packager}=require('@electron/packager');
(async()=>{
 const root=path.resolve(__dirname,'..');
 const worker=path.join(root,'dist-v4-core','worker');
 if(!fs.existsSync(path.join(worker,'helper-core.exe')))throw Error('Python worker missing. Build it first.');
 const result=await packager({dir:__dirname,name:'LinkVideo.Helper.4',platform:'win32',arch:'x64',out:path.join(root,'dist-v4'),overwrite:true,asar:true,prune:true,icon:path.join(root,'icon.ico'),extraResource:[worker],ignore:[/^\/test(?:\/|$)/,/^\/test_worker\.py$/,/^\/__pycache__(?:\/|$)/,/^\/operations\.py$/,/^\/worker\.py$/,/^\/package-win\.cjs$/,/^\/installer\.iss$/]});
 console.log(result.join('\n'));
})().catch(e=>{console.error(e);process.exitCode=1});
