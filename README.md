# money's helper

A fast, local CLI AI agent that controls my Windows PC — type (or say) a request and it opens apps, sends Discord DMs, types at human speed, creates folders, and writes files.

## Features

- **Local AI brain** — runs on [Ollama](https://ollama.com) with `qwen2.5-coder:7b`, which streams back strict JSON actions word-by-word. No API keys, nothing leaves your machine.
- **Discord DM automation** — focuses Discord, opens the Quick Switcher (`Ctrl+K`), searches the recipient, opens the DM, and sends your message.
- **Human-like typing** — types at ~170 WPM via `pyautogui`, so Discord shows a real "typing…" indicator instead of pasting a wall of text.
- **App control** — opens or focuses any app by name (Discord via its `discord://` protocol, anything else via Start-menu search).
- **Files & folders** — create directories, write code files, and open paths in VS Code straight from a prompt.
- **Voice mode ("Jarvis")** — a background wake-word listener waits for "Jarvis", records the next sentence, and runs it exactly like typed input.
- **J.A.R.V.I.S. personality** — a formal, slightly witty British-butler persona, with conversation memory (last 10 messages) and action chaining.

## Tech Stack

- **Python 3.10+**
- **[Ollama](https://ollama.com)** (`qwen2.5-coder:7b`) — local LLM inference
- **requests** — Ollama HTTP streaming
- **pyautogui** + **pygetwindow** — keyboard/mouse control and window focusing
- **SpeechRecognition** + **pyaudio** — voice wake-word and dictation
- **colorama** — terminal colors
- **ddgs** — DuckDuckGo search fallback
- **FastAPI** + **uvicorn** — optional local HTTP endpoint mode

## How to run it locally

1. **Install [Ollama](https://ollama.com/download)** and make sure it's running (port `11434`).
2. **Pull the model** (once):

   ```bash
   ollama pull qwen2.5-coder:7b
   ```

3. **Install the Python dependencies:**

   ```bash
   py -m pip install colorama pyautogui pygetwindow requests SpeechRecognition pyaudio ddgs fastapi uvicorn
   ```

4. **Run the agent:**

   ```bash
   py cli_agent.py
   ```

Type a request and press Enter — the assistant streams its reply, executes the action, and confirms with `Done.` Voice mode is on by default: just say "Jarvis".

> **Safety:** `pyautogui.FAILSAFE` is on — slam your mouse into the top-left corner at any time to abort an in-progress action.

---

Built by [Qrdz0](https://github.com/Qrdz0). I vibecode for fun, but I always try to learn a bit.
