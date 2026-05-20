import { get } from './config';

export async function handleCommand(command) {
  const cmd = command.trim();

  if (cmd.startsWith('request-')) {
    return await requestMedia(cmd.slice(8).trim());
  }

  return null;
}

async function requestMedia(title) {
  const baseUrl = get('OVERSEERR_URL', 'http://localhost:5055');
  const apiKey = get('OVERSEERR_API_KEY');
  const headers = { 'X-Api-Key': apiKey, 'Content-Type': 'application/json' };

  try {
    const searchResp = await fetch(
      `${baseUrl}/api/v1/search?query=${encodeURIComponent(title)}`,
      { headers }
    );
    if (!searchResp.ok) throw new Error(`Search failed: ${searchResp.status}`);

    const { results } = await searchResp.json();
    if (!results?.length) return `I couldn't find "${title}" on Overseerr.`;

    const { mediaType, id, title: movieTitle, name: showName } = results[0];
    const displayTitle = movieTitle || showName || title;

    const body = { mediaType, mediaId: id };
    if (mediaType === 'tv') body.seasons = 'all';

    const reqResp = await fetch(`${baseUrl}/api/v1/request`, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
    });

    if (reqResp.status === 201) return `Request submitted for "${displayTitle}".`;
    if (reqResp.status === 409) return `"${displayTitle}" has already been requested.`;
    return `The request for "${displayTitle}" failed (${reqResp.status}).`;
  } catch (e) {
    return `Couldn't reach Overseerr: ${e.message}`;
  }
}
