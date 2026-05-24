// ARIA config — AHAS-backed
// On local network: uses CT117 directly
// External: uses ahas.call-on.media

const CONFIG_DEFAULTS = {
  AHAS_API_URL: 'http://192.168.0.60:5000',
  OVERSEERR_URL: '',
  OVERSEERR_API_KEY: '',
};

let _config = { ...CONFIG_DEFAULTS };

export async function loadConfig() {
  // Try AHAS API health check to confirm server is reachable
  const url = (_config.AHAS_API_URL || CONFIG_DEFAULTS.AHAS_API_URL);
  try {
    const resp = await fetch(`${url}/health`, { cache: 'no-store' });
    if (resp.ok) {
      const data = await resp.json();
      console.log('[ARIA] AHAS connected:', data.service, data.version || '');
    }
  } catch (e) {
    console.warn('[ARIA] AHAS not reachable, will use defaults:', e.message);
    // Fall back to external URL if local fails
    _config.AHAS_API_URL = 'https://ahas.call-on.media';
  }
}

export function get(key, defaultValue = '') {
  return _config[key] !== undefined ? _config[key] : defaultValue;
}

export function set(key, value) {
  _config[key] = value;
}
