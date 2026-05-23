"""
AHAS -- assist.py
Claude (Anthropic) replaces OpenAI for intelligence.
edge-tts replaces OpenAI TTS (free, high quality, British voice).
"""

import anthropic
import asyncio
import edge_tts
import pygame
import os
import tempfile
from config import (
    ANTHROPIC_API_KEY, CLAUDE_MODEL, SYSTEM_PROMPT,
    TTS_ENGINE, TTS_VOICE, OPENAI_TTS_VOICE, OWNER_NAME
)

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

_conversation_history = []
MAX_HISTORY = 20


def ask_question_memory(question: str) -> str:
    global _conversation_history

    _conversation_history.append({"role": "user", "content": question})

    if len(_conversation_history) > MAX_HISTORY * 2:
        _conversation_history = _conversation_history[-(MAX_HISTORY * 2):]

    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=_conversation_history,
        )
        reply = response.content[0].text
        _conversation_history.append({"role": "assistant", "content": reply})
        return reply

    except anthropic.AuthenticationError:
        return "Authentication failed. Please check your Anthropic API key."
    except Exception as e:
        return f"I encountered an error: {str(e)}"


def clear_memory():
    global _conversation_history
    _conversation_history = []
    return "Memory cleared."


pygame.mixer.init()


async def _edge_tts_generate(text: str, output_path: str):
    communicate = edge_tts.Communicate(text, TTS_VOICE)
    await communicate.save(output_path)


def TTS(text: str) -> str:
    if not text.strip():
        return "done"

    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp_path = tmp.name
    tmp.close()

    try:
        if TTS_ENGINE == "edge":
            asyncio.run(_edge_tts_generate(text, tmp_path))

        elif TTS_ENGINE == "openai":
            from openai import OpenAI
            oai = OpenAI()
            response = oai.audio.speech.create(
                model="tts-1", voice=OPENAI_TTS_VOICE, input=text
            )
            response.stream_to_file(tmp_path)

        elif TTS_ENGINE == "pyttsx3":
            import pyttsx3
            engine = pyttsx3.init()
            engine.save_to_file(text, tmp_path)
            engine.runAndWait()

        pygame.mixer.music.load(tmp_path)
        pygame.mixer.music.play()
        import time
        while pygame.mixer.music.get_busy():
            time.sleep(0.1)
        pygame.mixer.music.unload()

    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass

    return "done"


def speak(text: str):
    speech = text.split("#")[0].strip()
    if speech:
        TTS(speech)
