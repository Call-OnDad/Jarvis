"""
AHAS -- ahas.py
Main loop. Say "AHAS" or "Jarvis" to wake.
"""

import time
import assist
import tools
from RealtimeSTT import AudioToTextRecorder
from config import HOT_WORDS, ASSISTANT_NAME, OWNER_NAME

if __name__ == "__main__":

    print(f"[{ASSISTANT_NAME}] Initialising voice pipeline...")

    recorder = AudioToTextRecorder(
        spinner=False,
        model="tiny.en",
        language="en",
        post_speech_silence_duration=0.1,
        silero_sensitivity=0.4,
    )

    skip_hot_word_check = False

    print(f"[{ASSISTANT_NAME}] Online. Listening for: {', '.join(HOT_WORDS)}")
    assist.TTS(f"Good day, {OWNER_NAME}. {ASSISTANT_NAME} is online and ready.")

    while True:
        current_text = recorder.text()

        if not current_text:
            continue

        print(f"[Heard] {current_text}")

        hot_word_detected = any(hw in current_text.lower() for hw in HOT_WORDS)

        if hot_word_detected or skip_hot_word_check:

            print(f"[{OWNER_NAME}] {current_text}")
            recorder.stop()

            timestamped = current_text + f"  [{time.strftime('%Y-%m-%d %H:%M:%S')}]"

            response = assist.ask_question_memory(timestamped)
            print(f"[{ASSISTANT_NAME}] {response}")

            assist.speak(response)

            if "#" in response:
                parts   = response.split("#")
                command = parts[1].strip() if len(parts) > 1 else ""
                if command:
                    tools.parse_command(command)

            speech_part = response.split("#")[0]
            skip_hot_word_check = "?" in speech_part

            recorder.start()
