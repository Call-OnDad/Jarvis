const CONFIG_SERVER = 'http://192.168.0.6/get_config.php';

let _config = {};

export async function loadConfig() {
  try {
    const resp = await fetch(CONFIG_SERVER, { cache: 'no-store' });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    _config = await resp.json();
    console.log('[ARIA] Config loaded from server');
  } catch (e) {
    console.warn('[ARIA] Config server unreachable:', e.message);
  }
}

export function get(key, defaultValue = '') {
  return _config[key] || defaultValue;
}
