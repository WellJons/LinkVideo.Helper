'use strict';
const {contextBridge,ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('helper',Object.freeze({
 login:(username,password,remember)=>ipcRenderer.invoke('helper:login',{username,password,remember}),
 rememberStatus:()=>ipcRenderer.invoke('helper:remember-status'),
 loginSaved:()=>ipcRenderer.invoke('helper:login-saved'),
 clearSaved:kind=>ipcRenderer.invoke('helper:clear-saved',{kind}),
 adminLogin:(username,password,target,remember)=>ipcRenderer.invoke('helper:admin-login',{username,password,target,remember}),
 adminSaved:()=>ipcRenderer.invoke('helper:admin-saved'),
 updateCheck:()=>ipcRenderer.invoke('helper:update-check'),
 updateDownload:()=>ipcRenderer.invoke('helper:update-download'),
 updateInstall:()=>ipcRenderer.invoke('helper:update-install'),
 status:()=>ipcRenderer.invoke('helper:status'),
 search:(query,mode)=>ipcRenderer.invoke('helper:search',{query,mode}),
 servers:()=>ipcRenderer.invoke('helper:servers'),
 analyze:host=>ipcRenderer.invoke('helper:analyze',{host}),
 operation:(method,args)=>ipcRenderer.invoke('helper:operation',{method,args}),
 copyVpn:(host,login)=>ipcRenderer.invoke('helper:copy-vpn',{host,login}),
 fileAction:(method,args={})=>ipcRenderer.invoke('helper:file-action',{method,...args}),
 archiveDownload:mode=>ipcRenderer.invoke('helper:archive-download',{mode}),
 cancel:()=>ipcRenderer.invoke('helper:cancel'),
 copy:text=>ipcRenderer.invoke('helper:copy',{text}),
 onProgress:callback=>{const listener=(_event,p)=>callback(p);ipcRenderer.on('helper:progress',listener);return ()=>ipcRenderer.removeListener('helper:progress',listener)},
 logout:()=>ipcRenderer.invoke('helper:logout')
}));
