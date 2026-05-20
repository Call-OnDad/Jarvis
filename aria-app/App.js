import React, { useEffect, useRef, useState } from 'react';
import { FlatList, SafeAreaView, StyleSheet, View } from 'react-native';
import { StatusBar } from 'expo-status-bar';
import * as Speech from 'expo-speech';
import { loadConfig } from './src/config';
import { chat } from './src/claude';
import { startRecording, stopAndTranscribe } from './src/stt';
import { handleCommand } from './src/commands';
import VoiceOrb from './src/components/VoiceOrb';
import MessageBubble from './src/components/MessageBubble';

export default function App() {
  const [status, setStatus] = useState('idle');
  const [messages, setMessages] = useState([]);
  const listRef = useRef(null);

  useEffect(() => { loadConfig(); }, []);

  function addMessage(role, text) {
    setMessages(prev => [...prev, { id: `${Date.now()}-${Math.random()}`, role, text }]);
    setTimeout(() => listRef.current?.scrollToEnd({ animated: true }), 100);
  }

  async function handleOrbPress() {
    if (status === 'idle') {
      try {
        await startRecording();
        setStatus('listening');
      } catch (e) {
        addMessage('assistant', `Mic error: ${e.message}`);
      }
      return;
    }

    if (status === 'listening') {
      setStatus('thinking');
      try {
        const transcript = await stopAndTranscribe();
        if (!transcript) { setStatus('idle'); return; }

        addMessage('user', transcript);

        const reply = await chat(transcript);
        const hashIdx = reply.lastIndexOf('#');
        const clean = hashIdx >= 0 ? reply.slice(0, hashIdx).trim() : reply;
        const command = hashIdx >= 0 ? reply.slice(hashIdx + 1).trim() : null;

        addMessage('assistant', clean);
        if (command) handleCommand(command);

        setStatus('speaking');
        Speech.speak(clean, {
          onDone: () => setStatus('idle'),
          onError: () => setStatus('idle'),
        });
      } catch (e) {
        addMessage('assistant', `Error: ${e.message}`);
        setStatus('idle');
      }
      return;
    }

    if (status === 'speaking') {
      Speech.stop();
      setStatus('idle');
    }
  }

  return (
    <SafeAreaView style={styles.container}>
      <StatusBar style="light" />
      <FlatList
        ref={listRef}
        data={messages}
        keyExtractor={item => item.id}
        renderItem={({ item }) => (
          <MessageBubble role={item.role} text={item.text} />
        )}
        contentContainerStyle={styles.list}
        ListFooterComponent={<View style={{ height: 20 }} />}
      />
      <View style={styles.orbArea}>
        <VoiceOrb status={status} onPress={handleOrbPress} />
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#07070F' },
  list: { paddingHorizontal: 16, paddingTop: 20, flexGrow: 1 },
  orbArea: { alignItems: 'center', paddingVertical: 36 },
});
