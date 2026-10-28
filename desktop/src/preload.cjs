'use strict';
const {contextBridge, ipcRenderer} = require('electron');
contextBridge.exposeInMainWorld('acouagent', Object.freeze({
  status: () => ipcRenderer.invoke('desktop:status'),
  start: () => ipcRenderer.invoke('desktop:start'),
  stop: () => ipcRenderer.invoke('desktop:stop'),
  open: () => ipcRenderer.invoke('desktop:open'),
  dockerGuide: () => ipcRenderer.invoke('desktop:docker-guide'),
  copyAdminToken: () => ipcRenderer.invoke('desktop:copy-admin'),
  diagnostics: () => ipcRenderer.invoke('desktop:diagnostics'),
  backup: () => ipcRenderer.invoke('desktop:backup'),
  onState: callback => {const listener = (_event, value) => callback(value); ipcRenderer.on('desktop:state', listener); return () => ipcRenderer.removeListener('desktop:state', listener);}
}));
