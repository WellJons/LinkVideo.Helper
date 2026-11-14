'use strict';
const {contextBridge,ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('helper',Object.freeze({
 login:(username,password)=>ipcRenderer.invoke('helper:login',{username,password}),
 status:()=>ipcRenderer.invoke('helper:status'),
 search:(query,mode)=>ipcRenderer.invoke('helper:search',{query,mode}),
 servers:()=>ipcRenderer.invoke('helper:servers'),
 analyze:host=>ipcRenderer.invoke('helper:analyze',{host}),
 operation:(method,args)=>ipcRenderer.invoke('helper:operation',{method,args}),
 archiveDownload:mode=>ipcRenderer.invoke('helper:archive-download',{mode}),
 cancel:()=>ipcRenderer.invoke('helper:cancel'),
 copy:text=>ipcRenderer.invoke('helper:copy',{text}),
 onProgress:callback=>{const listener=(_event,p)=>callback(p);ipcRenderer.on('helper:progress',listener);return ()=>ipcRenderer.removeListener('helper:progress',listener)},
 logout:()=>ipcRenderer.invoke('helper:logout')
}));
