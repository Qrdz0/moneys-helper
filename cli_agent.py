#!/usr/bin/env python3
"""money's helper - a fast text-based CLI AI agent for Windows.

Type a command OR just say "Jarvis" out loud: Ollama (qwen2.5-coder:7b)
streams back strict JSON word-by-word, and the agent executes the
action(s) - open apps, Discord DM macro (search + send), human-speed
typing, folders, files - with a formal British-butler personality and
conversation memory (last 10 messages).

Voice mode runs on a background thread: a wake-word listener waits for
"Jarvis", records the next sentence, and processes it exactly like a
typed input - you can still type in the terminal at any time.

Install requirements:
    py -m pip install colorama pyautogui pygetwindow requests SpeechRecognition pyaudio ddgs fastapi uvicorn
"""

import json
import os
import re
import subprocess
import sys
import threading
import time

import requests
from colorama import Fore, Style, init as colorama_init
import pyautogui
import pygetwindow as gw
import speech_recognition as sr
import pyaudio   # microphone backend required by speech_recognition
try:
    from ddgs import DDGS            # current DuckDuckGo search package
except ImportError:
    from duckduckgo_search import DDGS   # old (deprecated) package name
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn

colorama_init(autoreset=True)

pyautogui.PAUSE = 0.02   # small pause between key events
pyautogui.FAILSAFE = True   # slam mouse into a top corner to abort typing

# human typing speed: 0.07s per character ~= 170 WPM
TYPE_INTERVAL = 0.07

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen2.5-coder:7b"   # or "phi4:14b"

VOICE_WAKE_WORD = "jarvis"   # background mic waits for this word

SYSTEM_PROMPT = r"""You are a sophisticated, highly formal British AI butler (like J.A.R.V.I.S. or Alfred). You must respond ONLY with a valid JSON object.
Actions available:
- Discord Message: {"action": "discord_message", "recipient": "username", "text": "message"}
- Open App: {"action": "open_app", "app": "AppName"}
- Type Text: {"action": "type_text", "text": "..."}
- Write Code File: {"action": "write_file", "path": "C:\\path", "content": "..."}
- Chat: {"action": "chat", "text": "..."}
- Web Search: {"action": "search_web", "query": "search terms"}
- Read File: {"action": "read_file", "path": "C:\\path\\to\\file.txt"}

When generating text for 'discord_message' or 'type_text', do NOT just copy the user. You must speak like a loyal, slightly witty British butler. Be highly formal, but vary your vocabulary. Do not repeat the phrase 'inform me of any updates or tasks'. If the user says 'tell them it is an AI', explicitly state that you are a digital assistant."""

# conversation memory: system prompt first, then user/assistant turns.
# Sent in full to Ollama on every request; trimmed to the last 10 messages.
chat_history = [{"role": "system", "content": SYSTEM_PROMPT}]

# which app the current action chain opened (type_text refocuses it)
CURRENT_CHAIN_APP = None

# only one request runs at a time (voice thread vs. typed input)
task_lock = threading.Lock()

# set to stop the background voice listener (Ctrl+C / exit)
voice_stop = threading.Event()


# ================================================================
# loading indicator (spins until the first streamed token arrives)
# ================================================================
class Spinner:
    FRAMES = "|/-\\"

    def __init__(self, message="Assistant"):
        self._message = message
        self._stop = threading.Event()
        self._thread = None
        self._stopped = False

    def _run(self):
        i = 0
        while not self._stop.is_set():
            frame = self.FRAMES[i % len(self.FRAMES)]
            sys.stdout.write(
                f"\r{Fore.CYAN}{Style.DIM}{self._message} {frame}{Style.RESET_ALL}"
            )
            sys.stdout.flush()
            i += 1
            time.sleep(0.1)
        # clear the spinner line
        sys.stdout.write("\r" + " " * (len(self._message) + 8) + "\r")
        sys.stdout.flush()

    def __enter__(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        if self._stopped:
            return
        self._stopped = True
        self._stop.set()
        self._thread.join(timeout=1.0)


# ================================================================
# Ollama - STREAMING (word-by-word)
# ================================================================
def stream_ollama(user_text, history):
    """Stream the model reply word-by-word to the terminal.

    `history` is the ENTIRE chat_history (system + prior turns).
    Returns the accumulated raw text, or raises on connection errors.
    """
    payload = {
        "model": MODEL,
        "messages": history,
        "stream": True,
        "options": {
            "temperature": 0.2,   # low temp = reliable JSON
            "num_predict": 150,   # cap tokens -> no filler, faster replies
        },
    }

    with requests.post(OLLAMA_URL, json=payload, timeout=180, stream=True) as resp:
        resp.raise_for_status()

        parts = []
        header_printed = False
        spinner = Spinner("Assistant")
        spinner.__enter__()
        try:
            for line in resp.iter_lines():
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                piece = (obj.get("message") or {}).get("content") or ""
                if not piece:
                    continue

                # first token: stop the spinner, open the response line
                if not header_printed:
                    header_printed = True
                    spinner.__exit__(None, None, None)
                    sys.stdout.write(Fore.CYAN + "Assistant: " + Style.RESET_ALL)

                parts.append(piece)
                # print the chunk IMMEDIATELY as it arrives (dim yellow =
                # the "thought process"; the final parsed reply prints bright)
                sys.stdout.write(Fore.YELLOW + Style.DIM + piece)
                sys.stdout.flush()
        except Exception:
            if not header_printed:
                spinner.__exit__(None, None, None)
            sys.stdout.write("\n")
            sys.stdout.flush()
            raise

        if not header_printed:
            spinner.__exit__(None, None, None)   # empty/failed stream
        # clean newline so the terminal looks tidy
        sys.stdout.write("\n")
        sys.stdout.flush()

    return "".join(parts)


def extract_json(text):
    """Pull the first JSON array OR object out of the reply.

    Arrays are checked first (agentic chains), then single objects.
    Handles prose/fences around the JSON.
    """
    m_arr = re.search(r"\[.*\]", text, re.DOTALL)
    if m_arr:
        try:
            data = json.loads(m_arr.group(0))
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("no JSON found")
    return json.loads(m.group(0))


def trim_history():
    """Keep chat_history at system prompt + last 10 messages."""
    if len(chat_history) > 11:
        del chat_history[1:len(chat_history) - 10]


# ================================================================
# window focus helpers (pygetwindow)
# ================================================================
def find_window(title_part):
    """Return ANY window whose title contains title_part (case-insensitive)."""
    matches = [
        w for w in gw.getAllWindows()
        if title_part.lower() in (w.title or "").lower()
    ]
    if not matches:
        return None
    # prefer a visible (non-minimized) window
    for w in matches:
        if not w.isMinimized:
            return w
    return matches[0]


def focus_window(win):
    """activate() -> wait 0.5s -> click the window center (guaranteed focus)."""
    try:
        if win.isMinimized:
            win.restore()
        win.activate()
    except Exception:
        # activate() can be blocked by Windows - force focus instead
        try:
            win.minimize()
            time.sleep(0.2)
            win.restore()
        except Exception:
            pass
    time.sleep(0.5)
    # click the window center so it DEFINITELY has foreground focus
    try:
        pyautogui.click(win.center)
    except Exception:
        pass


# ================================================================
# actions
# ================================================================
def launch_app(app):
    global CURRENT_CHAIN_APP

    # Discord: protocol launch via "start" (more reliable than os.startfile)
    if "discord" in app.lower():
        try:
            subprocess.Popen(["start", "discord://"], shell=True)
            return True
        except Exception:
            pass   # fall through to generic attempts

    # 1) protocol handler, e.g. "spotify:"
    try:
        os.startfile(app.lower() + "://")
        return True
    except OSError:
        pass
    # 2) registered name / executable, e.g. "notepad.exe"
    try:
        os.startfile(app)
        return True
    except OSError:
        pass
    # 3) Windows start-menu search fallback
    try:
        pyautogui.press("win")
        time.sleep(0.5)
        pyautogui.write(app, interval=TYPE_INTERVAL)
        time.sleep(1.0)
        pyautogui.press("enter")
        return True
    except Exception:
        return False


def open_app(app):
    global CURRENT_CHAIN_APP

    # already running? focus it instead of double-launching
    win = find_window(app)
    if win is not None:
        focus_window(win)          # activate + 0.5s + center click
        CURRENT_CHAIN_APP = app
        return True

    # not running? launch it...
    if not launch_app(app):
        return False
    CURRENT_CHAIN_APP = app

    # Discord: wait 4s to fully load, then find (any "discord" title) + focus
    if "discord" in app.lower():
        time.sleep(4)
        win = find_window("Discord")
        if win is not None:
            focus_window(win)
    return True


def human_type(text):
    """Type text out at ~170 WPM so Discord shows 'typing...' and never lags."""
    try:
        pyautogui.write(text, interval=TYPE_INTERVAL)   # interval = 0.07
    except Exception:
        # character-by-character fallback (skips anything untypable)
        for ch in text:
            try:
                pyautogui.press(ch)
                time.sleep(TYPE_INTERVAL)
            except Exception:
                pass


def type_text(text):
    # if this chain opened an app, refocus it so we never type into the terminal
    if CURRENT_CHAIN_APP:
        win = find_window(CURRENT_CHAIN_APP)
        if win is not None:
            focus_window(win)     # activate + 0.5s + center click
    human_type(text)


def create_folder(path):
    os.makedirs(path, exist_ok=True)


def write_file(path, content):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def open_vscode(path):
    subprocess.Popen(["code", path])


def search_web(query):
    """Fetch the top 3 DuckDuckGo results and print them to the terminal."""
    if not query:
        print(Fore.RED + "search_web: empty query" + Style.RESET_ALL)
        return False
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=3))
    except Exception as e:
        print(Fore.RED + f"web search failed: {e}" + Style.RESET_ALL)
        return False
    if not results:
        print(Fore.YELLOW + f'No results for "{query}".' + Style.RESET_ALL)
        return True
    print(Fore.CYAN + f'Search: "{query}"' + Style.RESET_ALL)
    for i, r in enumerate(results, 1):
        print(f"{i}. {r.get('title', '')}")
        print(f"   {r.get('href', '')}")
        body = (r.get("body") or "").strip()
        if body:
            print(Fore.YELLOW + Style.DIM + f"   {body}" + Style.RESET_ALL)
    return True


def read_file(path):
    """Read a text file with open() and print its contents."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError as e:
        print(Fore.RED + f"read_file failed: {e}" + Style.RESET_ALL)
        return False
    print(Fore.CYAN + f"--- {path} ({len(content)} chars) ---" + Style.RESET_ALL)
    print(content)
    print(Fore.CYAN + "--- end of file ---" + Style.RESET_ALL)
    return True


def execute(action):
    """Run one JSON action. Returns True on success."""
    kind = action.get("action")

    if kind == "chat":
        print(Fore.YELLOW + str(action.get("text", "")) + Style.RESET_ALL)
        return True

    if kind == "discord_message":
        recipient = str(action.get("recipient", ""))
        text = str(action.get("text", ""))

        # 1) find + activate the Discord window (launch it first if not running)
        win = find_window("Discord")
        if win is None:
            if not launch_app("Discord"):
                print(Fore.RED + "Discord window not found" + Style.RESET_ALL)
                return False
            time.sleep(4)
            win = find_window("Discord")
        if win is None:
            print(Fore.RED + "Discord window not found" + Style.RESET_ALL)
            return False
        try:
            if win.isMinimized:
                win.restore()
            win.activate()
        except Exception:
            pass
        time.sleep(1)

        # 2) open Discord's Quick Switcher search
        pyautogui.hotkey('ctrl', 'k')
        time.sleep(0.5)                                   # 3
        pyautogui.write(recipient)                        # 4
        time.sleep(0.5)                                   # 5
        pyautogui.press('enter')                          # 6 open the DM
        time.sleep(0.8)                                   # 7 wait for DM to load
        pyautogui.write(text, interval=0.07)              # 8 type at 170 WPM
        pyautogui.press('enter')                          # 9 send
        return True

    if kind == "open_app":
        return open_app(str(action.get("app", "")))
    if kind == "type_text":
        type_text(str(action.get("text", "")))
        return True
    if kind == "create_folder":
        create_folder(str(action.get("path", "")))
        return True
    if kind == "write_file":
        write_file(str(action.get("path", "")), str(action.get("content", "")))
        return True
    if kind == "open_vscode":
        open_vscode(str(action.get("path", "")))
        return True
    if kind == "search_web":
        return search_web(str(action.get("query", "")))
    if kind == "read_file":
        return read_file(str(action.get("path", "")))

    print(Fore.RED + f"Unknown action: {kind}" + Style.RESET_ALL)
    return False


def run_actions(data):
    """Execute a single action dict or a LIST of actions (agentic chain)."""
    global CURRENT_CHAIN_APP
    CURRENT_CHAIN_APP = None                     # fresh chain

    actions = data if isinstance(data, list) else [data]
    for i, act in enumerate(actions):
        if not isinstance(act, dict):
            print(Fore.RED + f"Bad action entry: {act!r}" + Style.RESET_ALL)
            continue
        if i > 0:
            time.sleep(1)                        # 1s pause between chained actions
        ok = execute(act)
        if ok and act.get("action") != "chat":
            print(Fore.GREEN + "Done." + Style.RESET_ALL)
    CURRENT_CHAIN_APP = None


# ================================================================
# request pipeline — shared by TYPED and VOICE input
# ================================================================
def handle_input(user_text):
    """Process one request end-to-end: remember it, ask Ollama, execute."""
    # voice thread and keyboard share this - never run two at once
    if not task_lock.acquire(blocking=False):
        print(Fore.CYAN + Style.DIM +
              "busy with another request - waiting..." + Style.RESET_ALL)
    try:
        # remember the user's message
        chat_history.append({"role": "user", "content": user_text})

        # --- ask Ollama (streams word-by-word, full chat_history) ---
        try:
            raw = stream_ollama(user_text, chat_history)
        except requests.ConnectionError:
            print(Fore.RED + "Cannot reach Ollama - is it running? (ollama serve)"
                  + Style.RESET_ALL)
            chat_history.pop()   # drop the message that never got a reply
            return
        except Exception as e:
            print(Fore.RED + f"Ollama error: {e}" + Style.RESET_ALL)
            chat_history.pop()
            return
        if not raw:
            chat_history.pop()
            return

        # remember what the assistant said
        chat_history.append({"role": "assistant", "content": raw})
        trim_history()

        # --- parse the finished stream (object or array), then execute ---
        try:
            data = extract_json(raw)
        except Exception as e:
            print(Fore.RED + f"Bad JSON from model ({e})" + Style.RESET_ALL)
            print(Fore.YELLOW + raw + Style.RESET_ALL)
            return

        run_actions(data)
        return raw
    finally:
        task_lock.release()


# ================================================================
# WEB API — FastAPI on :8000 so the browser can send commands
# ================================================================
app = FastAPI(title="money's helper web bridge")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])


class CliCommand(BaseModel):
    text: str


def reply_text(raw):
    """Pull a human-readable sentence out of the model's JSON reply."""
    try:
        data = extract_json(raw)
    except Exception:
        return raw
    if isinstance(data, dict):
        return str(data.get("text") or "Done.")
    if isinstance(data, list):
        parts = [str(a.get("text")) for a in data
                 if isinstance(a, dict) and a.get("text")]
        if parts:
            return " ".join(parts)
    return "Done."


@app.post("/cli_command")
def cli_command(cmd: CliCommand):
    """Accept {"text": "..."} from the web app, run it like typed input."""
    raw = handle_input(cmd.text)
    if raw is None:
        return JSONResponse(status_code=500,
                            content={"status": "error",
                                     "text": "Request failed - check the terminal."})
    return {"status": "ok", "text": reply_text(raw), "raw": raw}


def run_api_server():
    """Block forever inside this daemon thread (terminal keeps working)."""
    config = uvicorn.Config(app, host="127.0.0.1", port=8000,
                            log_level="warning")
    try:
        uvicorn.Server(config).run()
    except OSError as e:
        print(Fore.RED + f"web api off (port 8000 unavailable): {e}"
              + Style.RESET_ALL)


# ================================================================
# VOICE MODE — background thread, wake word "Jarvis"
# ================================================================
def voice_loop():
    """Listen forever; only act when the wake word is heard.

    - no wake word            -> stay silent, keep listening
    - "Jarvis"                -> print "Listening...", record the next
                                 sentence, transcribe it, process it
                                 exactly like a typed input
    - "Jarvis <command>"      -> process <command> right away
    """
    r = sr.Recognizer()
    r.dynamic_energy_threshold = True   # adapts to room noise

    try:
        mic = sr.Microphone()
        with mic as source:
            print(Fore.CYAN + Style.DIM +
                  f"voice: say \"{VOICE_WAKE_WORD}\" to trigger "
                  "(background mic is live)" + Style.RESET_ALL)
            r.adjust_for_ambient_noise(source, duration=0.5)

            while not voice_stop.is_set():
                # ---- 1) wait for ANY short phrase (silent when idle) ----
                try:
                    audio = r.listen(source, timeout=2, phrase_time_limit=4)
                except sr.WaitTimeoutError:
                    continue              # nothing said -> keep waiting
                try:
                    heard = r.recognize_google(audio)
                except sr.UnknownValueError:
                    continue              # unintelligible -> ignore
                except sr.RequestError as e:
                    print(Fore.RED + f"voice STT unavailable: {e}" + Style.RESET_ALL)
                    time.sleep(3)
                    continue

                # ---- 2) wake word gate ----
                if VOICE_WAKE_WORD not in heard.lower():
                    continue              # not for us -> stay silent

                print(Fore.CYAN + "Listening..." + Style.RESET_ALL)

                # "Jarvis open discord" -> command already inline;
                # plain "Jarvis"        -> record the next sentence
                inline = heard.lower().split(VOICE_WAKE_WORD, 1)[1].strip(" ?!.,'\"")
                if inline:
                    cmd = inline
                else:
                    try:
                        audio = r.listen(source, timeout=5, phrase_time_limit=8)
                        cmd = r.recognize_google(audio)
                    except sr.WaitTimeoutError:
                        print(Fore.CYAN + Style.DIM + "(nothing heard)" + Style.RESET_ALL)
                        continue
                    except sr.UnknownValueError:
                        continue
                    except sr.RequestError as e:
                        print(Fore.RED + f"voice STT unavailable: {e}" + Style.RESET_ALL)
                        continue

                cmd = cmd.strip()
                if not cmd:
                    continue

                # ---- 3) process it exactly like typed input ----
                print(Fore.CYAN + "You (voice): " + Style.RESET_ALL + cmd)
                handle_input(cmd)

    except OSError as e:
        print(Fore.RED + f"voice mode off - microphone problem: {e}" + Style.RESET_ALL)
    except Exception as e:
        print(Fore.RED + f"voice mode off - {e}" + Style.RESET_ALL)


# ================================================================
# main loop (typing still works while the voice thread listens)
# ================================================================
def main():
    print(Fore.CYAN + Style.BRIGHT + "money's helper - CLI AI agent" + Style.RESET_ALL)
    print(Fore.CYAN + Style.DIM +
          f"model: {MODEL}   (change MODEL in this file for phi4:14b)\n"
          f"voice: say \"{VOICE_WAKE_WORD}\" any time   |   type 'exit' or Ctrl+C to quit\n"
          f"api  : POST http://localhost:8000/cli_command   (browser bridge)"
          + Style.RESET_ALL)

    # background wake-word listener (typing keeps working in parallel)
    threading.Thread(target=voice_loop, daemon=True, name="voice-listener").start()
    # background web server (browser commands land in the same pipeline)
    threading.Thread(target=run_api_server, daemon=True, name="api-server").start()

    while True:
        try:
            user_text = input(Fore.CYAN + "You: " + Style.RESET_ALL).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_text:
            continue
        if user_text.lower() in ("exit", "quit"):
            break

        handle_input(user_text)

    voice_stop.set()   # tell the background listener to wind down
    print(Fore.CYAN + "Bye." + Style.RESET_ALL)


if __name__ == "__main__":
    main()
