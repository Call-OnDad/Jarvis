import React, { useEffect, useRef } from 'react';
import { Animated, Pressable, StyleSheet } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import * as Haptics from 'expo-haptics';

const ORB_SIZE = 140;
const RING_SIZE = ORB_SIZE + 28;

const COLORS = {
  idle:      ['#F0B429', '#C68000'],
  listening: ['#E05252', '#A01010'],
  thinking:  ['#4A90E2', '#1E4080'],
  speaking:  ['#2EC4B6', '#0A7A72'],
};

const PULSE = {
  idle:      { scale: 1.04, duration: 2000 },
  listening: { scale: 1.12, duration: 550 },
  thinking:  { scale: 1.07, duration: 850 },
  speaking:  { scale: 1.06, duration: 1100 },
};

export default function VoiceOrb({ status, onPress }) {
  const pulse = useRef(new Animated.Value(1)).current;
  const animRef = useRef(null);

  useEffect(() => {
    if (animRef.current) animRef.current.stop();
    const { scale, duration } = PULSE[status] || PULSE.idle;
    animRef.current = Animated.loop(
      Animated.sequence([
        Animated.timing(pulse, { toValue: scale, duration, useNativeDriver: true }),
        Animated.timing(pulse, { toValue: 1, duration, useNativeDriver: true }),
      ])
    );
    animRef.current.start();
    return () => animRef.current?.stop();
  }, [status]);

  async function handlePress() {
    await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    onPress();
  }

  const colors = COLORS[status] || COLORS.idle;

  return (
    <Pressable onPress={handlePress} style={styles.wrapper}>
      <Animated.View
        style={[
          styles.ring,
          { transform: [{ scale: pulse }], borderColor: colors[0] },
        ]}
      />
      <LinearGradient
        colors={colors}
        style={styles.orb}
        start={{ x: 0.2, y: 0 }}
        end={{ x: 0.8, y: 1 }}
      />
    </Pressable>
  );
}

const styles = StyleSheet.create({
  wrapper: {
    width: RING_SIZE,
    height: RING_SIZE,
    alignItems: 'center',
    justifyContent: 'center',
  },
  ring: {
    position: 'absolute',
    width: RING_SIZE,
    height: RING_SIZE,
    borderRadius: RING_SIZE / 2,
    borderWidth: 1.5,
    opacity: 0.45,
  },
  orb: {
    width: ORB_SIZE,
    height: ORB_SIZE,
    borderRadius: ORB_SIZE / 2,
    elevation: 12,
    shadowColor: '#F0B429',
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.6,
    shadowRadius: 20,
  },
});
