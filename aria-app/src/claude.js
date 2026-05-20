import { get } from './config';

const SYSTEM_PROMPT = `You are ARIA, an advanced AI assistant. You are warm, concise, and helpful. You never waffle.

You can request movies or TV shows on Plex by ending your reply with '#request-Exact Title Here'.
Only append a command if the user explicitly asks to add, get, or request something on Plex.
NEVER put a # anywhere except at the very end of your message.

Keep all responses under 2 sentences unless the user needs a detailed answer.
Address the user as "Sir" occasionally but not in every message.`;

let history = [];

export async function chat(userMessage) {
  const apiKey = get('ANTHROPIC_API_KEY');
  if (!apiKey) throw new Error('No Anthropic API key configured.');

  history.push({ role: 'user', content: userMessage });

  const response = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'x-api-key': apiKey,
      'anthropic-version': '2023-06-01',
      'content-type': 'application/json',
    },
    body: JSON.stringify({
      model: 'claude-haiku-4-5-20251001',
      max_tokens: 256,
      system: SYSTEM_PROMPT,
      messages: history,
    }),
  });

  if (!response.ok) {
    const err = await response.text();
    throw new Error(`Claude API error: ${err}`);
  }

  const data = await response.json();
  const reply = data.content[0].text;
  history.push({ role: 'assistant', content: reply });
  return reply;
}

export function resetHistory() {
  history = [];
}
