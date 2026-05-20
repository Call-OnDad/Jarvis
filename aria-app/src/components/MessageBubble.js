import React from 'react';
import { StyleSheet, Text, View } from 'react-native';

export default function MessageBubble({ role, text }) {
  const isUser = role === 'user';
  return (
    <View style={[styles.row, isUser ? styles.rowUser : styles.rowAria]}>
      <View style={[styles.bubble, isUser ? styles.bubbleUser : styles.bubbleAria]}>
        <Text style={[styles.text, isUser ? styles.textUser : styles.textAria]}>
          {text}
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  row: { marginVertical: 5, flexDirection: 'row' },
  rowUser: { justifyContent: 'flex-end' },
  rowAria: { justifyContent: 'flex-start' },
  bubble: {
    maxWidth: '80%',
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 18,
  },
  bubbleUser: {
    backgroundColor: '#1a2642',
    borderBottomRightRadius: 4,
  },
  bubbleAria: {
    backgroundColor: '#13131f',
    borderBottomLeftRadius: 4,
    borderWidth: 1,
    borderColor: '#1e1e3a',
  },
  text: { fontSize: 15, lineHeight: 22 },
  textUser: { color: '#E8E8F0' },
  textAria: { color: '#C8C8E0' },
});
