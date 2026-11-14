'use strict';
const {contextBridge,ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('helper',Object.freeze({
 login:(username,password)=>ipcRenderer.invoke('helper:login',{username,password}),
 status:()=>ipcRenderer.invoke('helper:status'),
 search:(query,mode)=>ipcRenderer.invoke('helper:search',{query,mode}),
 servers:()=>ipcRenderer.invoke('helper:servers'),
 analyze:host=>ipcRenderer.invoke('helper:analyze',{host}),
 logout:()=>ipcRenderer.invoke('helper:logout')
}));
