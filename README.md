# money's helper

A fast, streaming **CLI AI assistant for Windows** that turns natural-language requests into real actions on your PC. It runs on a local [Ollama](https://ollama.com) model (`qwen2.5-coder:7b` by default) and executes what the model returns — opening apps, sending Discord DMs, typing at human speed, creating folders, and writing files.

Everything runs **locally**: your prompts never leave your machine, and no API keys are required.

```
You: DM John on Discord that the meeting has been rescheduled to 3 PM

Assistant: {"action": "discord_message", "recipient": "John", "text": "Good day, John. This is to advise that our meeting has been formally rescheduled to 15:00 hours."}
Done.
```

## Features

- **Streaming responses** — replies are printed word-by-word as the model generates them (no waiting for a spinner to finish), with the raw JSON shown dimmed and the executed result shown bright.
- **Discord DM automation** — focuses (or launches) Discord, opens the Quick Switcher (`Ctrl+K`), searches for the recipient, opens the DM, types the message at ~170 WPM, and presses Enter to send.
- **Window-aware focus** — uses `pygetwindow` to find *any* window by title (case-insensitive), activates it, and clicks its center to guarantee focus, so keystrokes never land in the wrong window.
- **Human-speed typing** — `pyautogui.write(text, interval=0.07)` types at roughly 170 characters per minute, so Discord shows a proper "typing…" indicator instead of pasting a wall of text.
- **App launching** — focuses an already-running app by name, launches Discord via its `discord://` protocol, falls back to protocol/Start-menu search for anything else.
- **File & folder actions** — create directories and write code files straight from a prompt (executor also supports opening a path in VS Code).
- **Conversation memory** — the full chat history (system prompt + last 10 messages) is sent with every request, so the assistant remembers context instead of looping on itself.
- **Action chaining** — the executor accepts a JSON array of actions and runs them in order with a 1-second pause between steps.
- **Butler persona** — the system prompt gives it a formal, slightly witty British-butler voice with strict instructions to never blindly copy your wording when generating messages.
- **Zero config** — model, temperature, and typing speed are constants at the top of the file.

## Requirements

- **Windows 10/11** (uses `pygetwindow`, `pyautogui`, and Windows window activation)
- **Python 3.10+**
- **[Ollama](https://ollama.com/download)** running locally (default: `http://localhost:11434`)

## Installation

1. **Install Ollama** from <https://ollama.com/download> and make sure it is running (you should see it on port `11434`).

2. **Pull the model** (once):

   ```bash
   ollama pull qwen2.5-coder:7b
   ```

3. **Install the Python dependencies:**

   ```bash
   py -m pip install colorama pyautogui pygetwindow requests
   ```

4. **Download the script** — clone the repo or grab `cli_agent.py`.

## Usage

Run the agent:

```bash
py cli_agent.py
```

Type a request and press Enter. The assistant streams its reply, prints the JSON it decided on, executes it, and confirms with `Done.` Type `exit`, `quit`, or `Ctrl+C` to quit.

### Examples

```
You: open Discord
You: DM Sarah that the quarterly report has been submitted for review
You: open notepad
You: tell them I will be unavailable until further notice
```

Typical output:

```
Assistant: {"action": "discord_message", "recipient": "Sarah", "text": "Good afternoon. Kindly be advised that the quarterly report has been formally submitted for review. Yours faithfully, Darius."}
Done.
```

## Configuration

Edit the constants at the top of `cli_agent.py`:

| Constant | Default | Purpose |
|---|---|---|
| `MODEL` | `qwen2.5-coder:7b` | Ollama model — switch to e.g. `phi4:14b` for a stronger model |
| `OLLAMA_URL` | `http://localhost:11434/api/chat` | Ollama endpoint |
| `TYPE_INTERVAL` | `0.07` | Seconds per typed character (~170 WPM) |
| `SYSTEM_PROMPT` | — | Personality, advertised actions, and formatting rules |

## How it works

```
your prompt ──▶ Ollama (streamed JSON) ──▶ extract_json() ──▶ execute()
                                                              │
                        ┌──────────┬─────────────┬───────────┼──────────┐
                        ▼          ▼             ▼           ▼          ▼
                  discord_message open_app    type_text   write_file   chat
                  (Ctrl+K macro)  (focus/     (focus +    (makedirs +  (print)
                                   launch)     170 WPM)    write)
```

1. Your message and the rolling `chat_history` are streamed to Ollama.
2. Tokens print to the terminal as they arrive.
3. The finished reply is parsed for the first JSON object or array.
4. Each action is executed through `pyautogui` / `pygetwindow` / the filesystem.

## Safety notes

- **`pyautogui.FAILSAFE` is enabled** — slam your mouse into the top-left corner at any time to abort an in-progress typing or click sequence.
- The assistant types and presses keys with **your real input** — read the streamed JSON on screen before pressing on if you're unsure what it will do.
- The agent only has the permissions of your own user account; run it only when you're at the machine.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Cannot reach Ollama` | Start Ollama: run `ollama serve` (or launch the Ollama app) |
| Model not found | `ollama pull qwen2.5-coder:7b` |
| Keys typed into the wrong window | Click the target window first, or start your request with `open <app>` so the agent focuses it |
| Discord opens but nothing is typed | Wait for Discord to finish loading, then retry — the agent waits 4 s after launching |

## License

MIT — use it, break it, improve it.
