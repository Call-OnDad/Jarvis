// AHAS-backed Claude client
// Routes through AHAS API — no API key needed in app

import { get } from './config';

// Local history for display only
let history = [];

export async function chat(userMessage) {
  const apiUrl = get('AHAS_API_URL', 'http://192.168.0.60:5000');

  history.push({ role: 'user', content: userMessage });

  const response = await fetch(`${apiUrl}/api/ask`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ message: userMessage }),
  });

  if (!response.ok) {
    const err = await response.text();
    throw new Error(`AHAS API error: ${err}`);
  }

  const data = await response.json();
  if (data.error) throw new Error(data.error);

  const reply = data.reply;
  history.push({ role: 'assistant', content: reply });
  return reply;
}

export async function resetHistory() {
  history = [];
  const apiUrl = get('AHAS_API_URL', 'http://192.168.0.60:5000');
  try {
    await fetch(`${apiUrl}/api/clear`, { method: 'POST' });
  } catch (e) {
    console.warn('[ARIA] Could not clear server history:', e.message);
  }
}

export function getHistory() {
  return history;
}
