# Keena setup guide

This guide takes you from a fresh Windows PC to a working Keena, with a verification check at the end of every step.
Plan for **45 to 60 minutes**, most of it downloads.

## Contents

1. [Before you begin](#1-before-you-begin)
2. [Install the prerequisites](#2-install-the-prerequisites)
3. [Install Keena](#3-install-keena)
4. [Configure Keena](#4-configure-keena)
5. [First run](#5-first-run)
6. [Sign in to her browser](#6-sign-in-to-her-browser)
7. [Connect your phone (optional)](#7-connect-your-phone-optional)
8. [Start with Windows (optional)](#8-start-with-windows-optional)
9. [Everyday use](#9-everyday-use)
10. [Updating and uninstalling](#10-updating-and-uninstalling)
11. [Troubleshooting](#troubleshooting)
12. [Command reference](#command-reference)

---

## 1. Before you begin

### Check your hardware

Keena runs her language model and her speech recognition on your graphics card. **An NVIDIA GPU with at least 8 GB
of VRAM is required.**

| Component | Minimum | Recommended |
|---|---|---|
| Operating system | Windows 10 / 11, 64-bit | Windows 11 |
| Graphics card | NVIDIA RTX, 8 GB VRAM | NVIDIA, 12 GB+ VRAM |
| Memory (RAM) | 16 GB | 32 GB |
| Processor | 6 cores | 8 cores or more |
| Storage | 20 GB free, SSD | 40 GB free, SSD |
| Audio | Microphone and speakers | Headset or USB microphone |

To see your graphics card: press `Ctrl + Shift + Esc` to open Task Manager, choose **Performance**, then **GPU**.
The name is at the top right; **Dedicated GPU memory** must be 8.0 GB or more.

> **Not supported:** macOS, Linux, AMD and Intel graphics cards.

### What you will set up

| Item | Purpose | Cost |
|---|---|---|
| Ollama + Qwen 3 8B | Keena's brain, running locally | Free |
| Python 3.11 + packages | Keena's code, speech recognition, voice, HUD | Free |
| Google Chrome | Her own browser window for web tasks | Free |
| LangSearch API key | Web answers and recommendations (optional) | Free tier |
| Gemini API key | Understanding Tamil and Tanglish (optional) | Free tier |

---

## 2. Install the prerequisites

### 2.1 Update the NVIDIA driver

Download the latest driver for your card from [nvidia.com/drivers](https://www.nvidia.com/Download/index.aspx), or use
the NVIDIA app, then restart the PC.

**Verify.** Open PowerShell (Start menu, type *PowerShell*) and run:

```powershell
nvidia-smi
```

You should see your card's name and a memory total of about 8000 MiB or more.

### 2.2 Install Python 3.11

Download **Python 3.11.x** (not a newer version) from [python.org/downloads](https://www.python.org/downloads/).
In the installer, tick **Add python.exe to PATH**, then choose **Install Now**.

**Verify.**

```powershell
py -3.11 --version
```

Expected: `Python 3.11.x`.

### 2.3 Install Git and Google Chrome

- Git: [git-scm.com](https://git-scm.com), default options.
- Google Chrome: [google.com/chrome](https://www.google.com/chrome/), if not already installed.

**Verify.**

```powershell
git --version
```

### 2.4 Install Ollama and download the model

Install Ollama from [ollama.com](https://ollama.com). It runs in the background (look for its icon in the system
tray). Then download Keena's brain, about 5.2 GB:

```powershell
ollama pull qwen3:8b
```

**Verify.**

```powershell
ollama run qwen3:8b "Say hello in five words"
ollama ps
```

The model should answer, and `ollama ps` should show it at **100% GPU**. If it says CPU, close other GPU-heavy
programs and check the driver.

---

## 3. Install Keena

### 3.1 Download the code

```powershell
cd $HOME
git clone https://github.com/WeDigiStudio/Keena-Personal-Assistant.git keena
cd keena
```

### 3.2 Create a virtual environment

A virtual environment keeps Keena's packages separate from the rest of your system.

```powershell
py -3.11 -m venv .venv
.venv\Scripts\activate
```

Your prompt now starts with `(.venv)`. **Every time you open a new PowerShell window for Keena, run
`.venv\Scripts\activate` first.**

> If PowerShell says running scripts is disabled, run this once, then activate again:
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
> ```

### 3.3 Install the packages

```powershell
pip install -r requirements.txt
pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"
```

The second line installs the NVIDIA libraries that let speech recognition run on the GPU.

**Verify.**

```powershell
python -c "import faster_whisper, ollama, playwright, PySide6; print('Keena packages OK')"
```

---

## 4. Configure Keena

### 4.1 Create your settings file

```powershell
copy .env.example .env
notepad .env
```

Change at least this line, then save:

```ini
JARVIS_USER=YourName
```

Everything else already has sensible defaults. The full list of settings is in the
[README](../README.md#configuration).

### 4.2 Add the free API keys (recommended)

Without these keys Keena still works, but she cannot search the web or understand Tamil.

**LangSearch** (web answers, news, prices, recommendations)

1. Sign up at [langsearch.com](https://langsearch.com).
2. Create an API key in your dashboard.
3. Paste it into `.env`: `LANGSEARCH_API_KEY=your-key`

**Google Gemini** (understanding Tamil and Tanglish speech)

1. Go to [aistudio.google.com](https://aistudio.google.com) and sign in with a Google account.
2. Choose **Get API key**, then **Create API key**.
3. Paste it into `.env`: `GEMINI_API_KEY=your-key`

> Paste keys with no quotes and no spaces. **Never share your `.env` file or post your keys online.**

### 4.3 Tell Keena about yourself

Create a file named `about_me.txt` in the `keena` folder, with one fact per line:

```text
Name: Your Name
City: Chennai
Work: Founder of ...
Favourite food: Biryani
Best friend: ...
```

Keena reads this whenever you talk about yourself and never invents facts that aren't in it. You can add more at any
time by saying *"Keena, remember that ..."*.

### 4.4 Help her hear names (optional)

Speech recognition may mishear uncommon names. List them in `.env`, separated by commas:

```ini
HEAR_NAMES=Arjun,Priya,PixelCraft
KNOWN_EMAIL_DOMAINS=yourcompany.com
```

`KNOWN_EMAIL_DOMAINS` lets her correct email addresses you dictate (for example, a misheard company domain).

---

## 5. First run

Run these in order inside the activated virtual environment.

### 5.1 Microphone test

```powershell
python main.py --mic-test
```

Speak normally: the level should rise well above the silence value. Press `Ctrl + C` to stop. If the level barely
moves, pick a different microphone with `MIC_DEVICE` in `.env`.

### 5.2 Text mode

```powershell
python main.py --text
```

Type *hello* and *what's the weather*. This checks the brain and the tools without the microphone. Type `quit` to exit.

### 5.3 Voice mode

```powershell
python main.py
```

The first start downloads the speech model (about 500 MB). When you see a pink orb in the corner of the screen and
`Waiting for "Keena"`, say **"Hey Keena"**, wait for the soft chime, and speak. After she answers you can keep talking
for a few seconds without saying her name again.

**Verify.** Ask *"What time is it?"* (instant) and *"How are you?"* (a chat reply).

---

## 6. Sign in to her browser

The first time Keena opens a website, a **separate Chrome window** appears. It uses its own Chrome profile, because
Chrome only allows programs to control a separate profile. Your normal Chrome, passwords and logins are not touched.

Say *"Keena, open Gmail"* and sign in once in that window, and to YouTube or Amazon if you want her to use them. The
logins are kept for next time.

---

## 7. Connect your phone (optional)

Your phone and PC must be on the same Wi-Fi network. With Keena running, open a second PowerShell window:

```powershell
cd $HOME\keena
.venv\Scripts\activate
python -m remote.pair
```

1. Scan the QR code with your phone's camera.
2. The browser warns **"Your connection is not private"**. This is expected: your PC created its own certificate.
   The connection is still encrypted. Choose **Advanced**, then **Proceed**.
3. Allow the microphone.
4. Add the page to your home screen (Chrome menu, **Add to Home screen**). It opens like an app.
5. Tap the reactor once (phones require a tap before allowing sound and the microphone), then say *"Hey Keena ..."*.
   It listens while the page is open on screen.

If Windows asks whether Python may use the network, allow it on **private networks**.

**Outside your home network:** install [Tailscale](https://tailscale.com) on the PC and the phone, sign in with the same
account, run `tailscale serve --bg 8765` once on the PC, then run `python -m remote.pair` again and scan the new code.

---

## 8. Start with Windows (optional)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1
```

Keena then starts minimised when you log in and restarts automatically if she ever stops unexpectedly. To turn it off:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 remove
```

---

## 9. Everyday use

| Say | Result |
|---|---|
| "Hey Keena, how are you?" | A chat reply, in character |
| "Play Believer" / "pause" / "next song" | Music on YouTube |
| "Volume 30" / "take a screenshot" / "lock the PC" | Instant PC control |
| "What's the latest on ...?" | Web answer with the source |
| "Suggest a good Vijay movie" | Two or three real picks and her favourite |
| "Search Amazon for a gaming mouse" / "open the cheapest one" / "what's the price?" | Shopping in her Chrome window |
| "Draft a mail to Arjun about the demo and send it" | Gmail opens, the mail types itself, she asks before sending |
| "Remind me to drink water in 30 minutes" | A reminder, announced on time |
| "Remember that ..." / "What do you know about me?" / "Forget that" | Memory |
| "Big screen mode" / "back to normal" | Full-screen HUD and back |
| "Goodbye Keena" | Shuts Keena down |

To see what she has remembered, or your conversation log:

```powershell
python -m brain.memory
python -m brain.log
```

---

## 10. Updating and uninstalling

**Update to the latest version**

```powershell
cd $HOME\keena
.venv\Scripts\activate
git pull
pip install -r requirements.txt
```

Your `.env`, `about_me.txt` and `data` folder are not affected.

**Uninstall**

1. Turn off autostart if you enabled it (section 8).
2. Delete the `keena` folder. This also deletes her memories and her browser profile.
3. Optionally remove the model: `ollama rm qwen3:8b`, and uninstall Ollama.

---

## Troubleshooting

| Symptom | Cause | Resolution |
|---|---|---|
| *Can't reach Ollama* at startup | Ollama isn't running | Start Ollama from the Start menu, wait a few seconds, run Keena again. |
| *Model 'qwen3:8b' isn't downloaded* | Model missing | Run `ollama pull qwen3:8b`. |
| Very slow replies; `ollama ps` shows CPU | Not enough free VRAM | Close games and GPU-heavy apps; confirm the card has 8 GB or more. |
| She never reacts to "Hey Keena" | Wrong microphone or level | Run `python main.py --mic-test`; set `MIC_DEVICE`; speak closer to the mic. |
| She cuts you off mid-sentence | Silence detected too early | Raise `SILENCE_SECONDS` (for example to `0.9`) or lower `SILENCE_RMS`. |
| She keeps listening after you stop | Background noise above the threshold | Raise `SILENCE_RMS`; use a headset. |
| No voice, or *Voice error 403* | Internet issue or an outdated voice package | Check the connection; run `pip install -U edge-tts`. |
| Whisper GPU error at startup | NVIDIA libraries missing | Re-run the `nvidia-cublas-cu12` / `nvidia-cudnn-cu12` line from 3.3. She falls back to the CPU meanwhile. |
| Gmail opens a Google marketing page | Her browser isn't signed in | Sign in once in her Chrome window (section 6). |
| *Web search isn't set up* | No LangSearch key | Add `LANGSEARCH_API_KEY` to `.env` and restart. |
| Tamil isn't understood | No Gemini key, or the free daily limit was reached | Add `GEMINI_API_KEY`; after the daily limit she uses English recognition until the next day. |
| Phone page doesn't load | Different network or firewall | Same Wi-Fi as the PC; allow Python in Windows Firewall; run `python -m remote.pair` again. |
| Phone microphone blocked | Page opened without HTTPS, or permission denied | Use the `https://` QR code; allow the microphone in the browser's site settings. |
| *Port 8765 busy* | Keena is already running | Close the other Keena window first. |

Still stuck? Run `python main.py --text` to check the brain and tools without audio, and read the messages printed in
the PowerShell window: they explain most problems.

---

## Command reference

| Command | Purpose |
|---|---|
| `python main.py` | Start Keena (voice mode) |
| `python main.py --text` | Type instead of speaking |
| `python main.py --mic-test` | Show microphone levels |
| `python main.py --brain-test` | Check model speed and tool calling |
| `python hud.py --demo` | Preview the HUD |
| `python hud.py --demo --big` | Preview the full-screen HUD |
| `python -m remote.pair` | Show the phone pairing QR code (`--new` creates a new key) |
| `python -m brain.memory` | List remembered facts |
| `python -m brain.log` | Show the conversation log |
| `run_jarvis.cmd` | Start Keena with automatic restart after a crash |
| `scripts\autostart.ps1` | Start with Windows (`remove` to turn off) |
