import { Audio } from 'expo-av';
import { get } from './config';

let _recording = null;

export async function startRecording() {
  const { granted } = await Audio.requestPermissionsAsync();
  if (!granted) throw new Error('Microphone permission denied.');

  await Audio.setAudioModeAsync({
    allowsRecordingIOS: true,
    playsInSilentModeIOS: true,
  });

  const { recording } = await Audio.Recording.createAsync(
    Audio.RecordingOptionsPresets.HIGH_QUALITY
  );
  _recording = recording;
}

export async function stopAndTranscribe() {
  if (!_recording) return '';

  await _recording.stopAndUnloadAsync();
  const uri = _recording.getURI();
  _recording = null;

  await Audio.setAudioModeAsync({ allowsRecordingIOS: false });

  const apiKey = get('OPENAI_API_KEY');
  if (!apiKey) throw new Error('No OpenAI API key configured for transcription.');

  const formData = new FormData();
  formData.append('file', { uri, name: 'audio.m4a', type: 'audio/m4a' });
  formData.append('model', 'whisper-1');

  const resp = await fetch('https://api.openai.com/v1/audio/transcriptions', {
    method: 'POST',
    headers: { Authorization: `Bearer ${apiKey}` },
    body: formData,
  });

  if (!resp.ok) {
    const err = await resp.text();
    throw new Error(`Whisper error: ${err}`);
  }

  const data = await resp.json();
  return data.text?.trim() || '';
}

export async function cancelRecording() {
  if (!_recording) return;
  try {
    await _recording.stopAndUnloadAsync();
  } catch (_) {}
  _recording = null;
  await Audio.setAudioModeAsync({ allowsRecordingIOS: false });
}
