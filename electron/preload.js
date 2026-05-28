'use strict';
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  selectDirectory: () => ipcRenderer.invoke('select-directory'),
  openPath: (p) => ipcRenderer.invoke('open-path', p),
  getVersion: () => ipcRenderer.invoke('app-version'),
});
