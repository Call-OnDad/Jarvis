const { app, BrowserWindow, Tray, Menu, nativeImage, shell, ipcMain } = require('electron');
const path = require('path');

const AHAS_LOCAL    = 'http://192.168.0.60:5000';
const AHAS_EXTERNAL = 'https://ahas.call-on.media';

let mainWindow = null;
let tray = null;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400, height: 900, minWidth: 900, minHeight: 600,
    frame: false,
    backgroundColor: '#020b18',
    icon: path.join(__dirname, 'assets', 'icon.png'),
    webPreferences: { nodeIntegration: false, contextIsolation: true, webSecurity: false },
    title: 'AHAS — Home Intelligence',
  });

  mainWindow.loadURL(AHAS_LOCAL).catch(() => mainWindow.loadURL(AHAS_EXTERNAL));

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  mainWindow.on('closed', () => { mainWindow = null; });
}

function createTray() {
  try { tray = new Tray(path.join(__dirname, 'assets', 'tray.png')); }
  catch { tray = new Tray(nativeImage.createEmpty()); }

  tray.setToolTip('AHAS — Home Intelligence');
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'AHAS', enabled: false },
    { type: 'separator' },
    { label: 'Show',           click: () => mainWindow ? mainWindow.show() : createWindow() },
    { label: 'Home Assistant', click: () => shell.openExternal('https://ha.call-on.media') },
    { label: 'Proxmox',        click: () => shell.openExternal('https://192.168.0.10:8006') },
    { label: 'n8n',            click: () => shell.openExternal('http://192.168.0.28:5678') },
    { type: 'separator' },
    { label: 'Quit', click: () => app.quit() },
  ]));

  tray.on('double-click', () => mainWindow ? mainWindow.show() : createWindow());
}

app.whenReady().then(() => { createWindow(); createTray(); });
app.on('window-all-closed', e => { if (process.platform !== 'darwin') e.preventDefault(); });
app.on('activate', () => { if (!mainWindow) createWindow(); });

ipcMain.on('window-minimize', () => mainWindow?.minimize());
ipcMain.on('window-maximize', () => mainWindow?.isMaximized() ? mainWindow.restore() : mainWindow?.maximize());
ipcMain.on('window-close',    () => mainWindow?.hide());
