'use strict';
const {contextBridge,ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('wizard',{
 info:()=>ipcRenderer.invoke('wizard:info'),
 run:option=>ipcRenderer.invoke('wizard:run',{option}),
 close:launch=>ipcRenderer.invoke('wizard:close',{launch}),
 progress:fn=>{if(typeof fn==='function')ipcRenderer.on('wizard:progress',(_,p)=>fn(p))}
});
