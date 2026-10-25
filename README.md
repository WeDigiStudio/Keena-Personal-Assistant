# Keena: your own AI companion, running on your PC

Keena is a voice AI companion for Windows. Say **"Hey Keena"** and talk to her in English, Tamil or Tanglish: she
answers in English, chats like a friend, remembers what you ask her to remember, and actually controls your PC.

- **Talks like a person:** playful, warm, fires you up on a rough day; recommendations come from the web, never invented
- **Controls your PC:** apps, volume, media, screenshots, reminders, timers, notes, weather
- **YouTube and the web:** "play Believer", web answers with sources, "what's the latest on ..."
- **Drives her own Chrome:** search Amazon / Flipkart, "open the cheapest one", "what's the price?", "add it to cart"
- **Email:** Gmail opens, the mail types itself, and she sends it only after you say yes
- **Phone remote:** a pink HUD page on your phone, hands-free "Hey Keena" over your home Wi-Fi
- **Memory:** "remember that ...", "what do you know about me?", "forget that", all stored on your PC

Her brain is **Qwen 3 8B**, running locally through [Ollama](https://ollama.com). No subscription.

## Read this first: requirements

| | Minimum | Recommended |
|---|---|---|
| OS | Windows 10 / 11, 64-bit | Windows 11 |
| GPU | **NVIDIA RTX with 8 GB VRAM** (RTX 3060 8 GB, 3060 Ti, 3070, 4060, 4060 Ti, 2070/2080) | 12 GB+ (RTX 3060 12 GB, 4070) |
| RAM | 16 GB | 32 GB |
| Storage | 20 GB free (SSD) | 40 GB |
| Other | Google Chrome, microphone, internet | headset |

Her brain (~5.6 GB) and her hearing (Whisper) share the graphics card: about 6.8 GB of an 8 GB card is in use.
Mac, Linux and AMD / Intel GPUs are not supported.

## Setup

The full step-by-step guide, with every command, is in **[docs/Keena_Setup_Guide.pdf](docs/Keena_Setup_Guide.pdf)**.
In short:

```powershell
# 1. Install: NVIDIA driver, Python 3.11, Git, Google Chrome, Ollama
ollama pull qwen3:8b

# 2. Get the code and install
git clone https://github.com/WeDigiStudio/Keena-Personal-Assistant.git keena
cd keena
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"

# 3. Settings: copy, then put your name and the two free keys in .env
copy .env.example .env
notepad .env

# 4. Run
python main.py --text      # type to test
python main.py             # say "Hey Keena"
python -m remote.pair      # optional: QR code for your phone
```

Free keys (optional, recommended): `LANGSEARCH_API_KEY` (langsearch.com) for web answers and recommendations,
`GEMINI_API_KEY` (aistudio.google.com) for understanding Tamil / Tanglish speech.
Create `about_me.txt` with one fact per line (`Name: ...`, `City: ...`) so she knows you.

## What runs where

| On your PC | Online (free services) |
|---|---|
| Her brain (Qwen 3 8B), English speech recognition (Whisper), wake word, memories, profile, conversation log, PC control, HUD, phone server | Her voice (Microsoft Edge voices), web search (LangSearch), Tamil speech understanding (Google Gemini), the sites she opens |

## Safety

She never sends an email, buys anything, or shuts down / sleeps the PC without your yes; never types into a terminal;
never invents email addresses, reminder times or facts about you (checked in code); remembers only what you ask; the
phone page needs a secret key and works only on your network. Everything personal stays in this folder:
`.env`, `about_me.txt` and `data/` are git-ignored, so never commit or share them.

## Project layout

```
main.py            voice loop: wake word, listening, follow-ups, HUD, timing
brain/             router (instant commands), brain (Qwen + tools + safety checks), companion (chat + personality),
                   memory, summaries, persona
tools/             PC tools, Keena's own Chrome (Playwright), web search
voice/             microphone, Whisper, wake word, voice, Tamil understanding, HUD client
remote/            phone remote (web page + server, QR pairing)
hud.py             the pink arc-reactor HUD (PySide6)
```

## License

MIT (see LICENSE). Built with Ollama + Qwen 3, faster-whisper, edge-tts, Playwright and PySide6.
