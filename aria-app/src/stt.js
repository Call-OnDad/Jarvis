// Speech-to-text using Web Speech API (works on Expo web, no API key needed)
// Falls back gracefully on native targets where SpeechRecognition is unavailable.

let _recognition = null;
let _resolveTranscript = null;
let _rejectTranscript = null;

function getSpeechRecognition() {
  if (typeof window === 'undefined') return null;
  return window.SpeechRecognition || window.webkitSpeechRecognition || null;
}

export async function startRecording() {
  const SpeechRecognition = getSpeechRecognition();
  if (!SpeechRecognition) {
    throw new Error('Speech recognition not supported in this environment.');
  }

  _recognition = new SpeechRecognition();
  _recognition.lang = 'en-GB';
  _recognition.interimResults = false;
  _recognition.maxAlternatives = 1;
  _recognition.continuous = false;

  // Start immediately — stopAndTranscribe() will stop it
  _recognition.start();
}

export function stopAndTranscribe() {
  return new Promise((resolve, reject) => {
    if (!_recognition) {
      resolve('');
      return;
    }

    _resolveTranscript = resolve;
    _rejectTranscript = reject;

    _recognition.onresult = (event) => {
      const transcript = event.results[0][0].transcript.trim();
      _recognition = null;
      resolve(transcript);
    };

    _recognition.onerror = (event) => {
      _recognition = null;
      reject(new Error(`Speech recognition error: ${event.error}`));
    };

    _recognition.onnomatch = () => {
      _recognition = null;
      resolve('');
    };

    _recognition.onend = () => {
      // onresult fires before onend; if we get here without a result, resolve empty
      if (_resolveTranscript === resolve) {
        _recognition = null;
        resolve('');
      }
    };

    try {
      _recognition.stop();
    } catch (e) {
      // already stopped — onend will fire
    }
  });
}

export function cancelRecording() {
  if (!_recognition) return;
  try {
    _recognition.abort();
  } catch (_) {}
  _recognition = null;
}
