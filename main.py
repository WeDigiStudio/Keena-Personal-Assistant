"""Jarvis, Phase 1: say "Hey Jarvis", give a command, it acts and talks back.

  python main.py             voice mode (the real thing)
  python main.py --text      type commands instead of speaking (test the brain + tools)
  python main.py --mic-test  print your mic loudness to tune SILENCE_RMS
  python main.py --brain-test  check the local model's speed and whether it calls tools
"""
import re
import sys
import threading
import time

import numpy as np

import config
from brain.brain import Brain
from brain.persona import line
from brain.router import screen_mode

STOP_PHRASES = ("goodbye jarvis", "shut down jarvis", "jarvis offline", "goodbye keena", "shut down keena", "keena offline",
                "goodbye kina", "kina offline")
DISMISS_PHRASES = ("that's all", "that is all", "thank you", "thanks", "nothing else", "go to sleep", "never mind")
YES_WORDS =("yes", "yeah", "yep", "sure", "do it", "go ahead", "confirm")


def is_dismiss(text):
    """'thanks', 'thank you so much jaws', 'that's all', 'ok bye' -> go back to sleep.
    Only SHORT goodbyes: "thanks for reminding, now tell me about X" is a new request, not a goodbye."""
    # Whisper sometimes doubles it: "Thank you so much. Thank you so much" -> say it once
    parts = [p.strip(" .!?,") for p in re.split(r"[.!?]+", text) if p.strip(" .!?,")]
    t = " ".join(dict.fromkeys(parts)) if parts else ""
    words = t.split()
    if not words or len(words) > 6:
        return False
    if any(p in t for p in DISMISS_PHRASES):
        return True
    return words[0] in ("thanks", "thank", "bye", "okay", "ok", "cool", "nice", "great")


def log_tool(name, args):
    print(f"   -> {name} {args}")


def text_mode():
    brain = Brain()
    confirm = lambda q: input(f"{config.ASSISTANT_NAME}: {q} (y/n) ").strip().lower().startswith("y")  # noqa: E731
    turn_lock = threading.Lock()

    def remote_run(text, ask_phone, speak, phone_card=None):
        with turn_lock:
            print(f"\n[phone] You: {text}")
            reply = brain.ask(text, ask_phone, on_tool=lambda n, a: (log_tool(n, a), phone_card and phone_card(n, a)))
            print(f"[phone] {config.ASSISTANT_NAME}: {reply}")
            return reply

    start_remote(remote_run)
    print("Text mode. Type a command, or 'quit'.")
    while True:
        text = input("\nYou: ").strip()
        if text.lower() in ("quit", "exit"):
            break
        if text:
            start = time.time()
            with turn_lock:
                reply = brain.ask(text, confirm, on_tool=log_tool)
            print(f"{config.ASSISTANT_NAME}: {reply}   ({time.time() - start:.1f}s)")
            if brain.ai_timing():
                print(f"   [timing] {brain.ai_timing()}")


def start_remote(run, stt=None):
    """Jarvis Remote (phone control), if enabled. Never stops Jarvis from starting."""
    if not config.REMOTE:
        return None
    try:
        from remote import server
        voice = dict(hear=server.make_hear(stt), voice=server.make_voice(config.VOICE, config.VOICE_RATE)) if stt else {}   # talk on the phone
        r = server.Remote(run, config.REMOTE_PORT, config.REMOTE_LAN, server.screenshot_jpeg, **voice,
                          https_port=config.REMOTE_HTTPS_PORT or None).start()
        where = "anywhere (Tailscale) + home Wi-Fi" if server.tailscale_ip() else "home Wi-Fi (Tailscale is off)"
        print(f"{config.ASSISTANT_NAME} Remote on port {config.REMOTE_PORT}: {where}. Pair a phone: python -m remote.pair")
        return r
    except OSError as e:
        print(f"Jarvis Remote not started (port {config.REMOTE_PORT} busy? another Jarvis running?): {e}")
    except Exception as e:
        print(f"Jarvis Remote not started: {e!r}")
    return None


def mic_test():
    from voice.audio import Mic, list_mics, rms
    from voice.wake import WakeWord

    print("Microphones found:")
    list_mics()
    print(f"\nUsing: {config.MIC_DEVICE if config.MIC_DEVICE is not None else 'Windows default'}"
          "  (change with MIC_DEVICE=<number> in .env)\n")
    wake = WakeWord(config.WAKE_THRESHOLD)
    mic = Mic()
    mic.start()
    print("Stay quiet, then talk, then say 'Hey Jarvis' a few times. Ctrl+C to stop.")
    print("LEVEL should jump when you talk. WAKE should go above "
          f"{config.WAKE_THRESHOLD} when you say Hey Jarvis.\n")
    try:
        while True:
            frame = mic.read()
            level = int(rms(frame))
            score = max(wake.model.predict(frame).values())
            hit = "  <<< HEY JARVIS DETECTED" if score >= config.WAKE_THRESHOLD else ""
            print(f"LEVEL {level:6d} {'#' * min(40, level // 50):<40} WAKE {score:.2f}{hit}")
    except KeyboardInterrupt:
        pass


def voice_mode():
    from voice.audio import Mic, chime, rms
    from voice.stt import SpeechToText, find_wake, _strip_wake as strip_wake
    from voice import language
    from voice.tts import TextToSpeech
    from voice.wake import WakeWord
    from voice.hud_client import Hud
    from tools.extras import due_reminders
    from tools import apps
    threading.Thread(target=apps.installed, daemon=True).start()  # index installed apps/games in the background
    from voice import language
    threading.Thread(target=language.warm, daemon=True).start()   # Gemini connection ready for his first Tamil sentence

    print("Loading models (first run downloads them, give it a minute)...")
    hud = Hud(config.HUD, config.HUD_POSITION, config.HUD_SCALE)   # the on-screen HUD (separate process)
    hud.boot("SYSTEMS")
    tts = TextToSpeech(config.VOICE, config.VOICE_RATE)
    # the HUD shows the sentence she is saying, typed at her speaking pace (long web answers raced ahead and the
    # part being spoken had already scrolled out of the corner)
    tts.on_play = lambda sentence: hud.send(reply=sentence, pace=1)
    if config.FILLERS:
        tts.prepare_fillers()
    hud.boot(f"VOICE · {config.VOICE.split('-')[-1].replace('Neural', '')}")
    stt = SpeechToText(config.WHISPER_MODEL, config.WHISPER_DEVICE)
    hud.boot(f"HEARING · {config.WHISPER_MODEL} · {'GPU' if stt.device == 'cuda' else 'CPU'}")
    wake = WakeWord(config.WAKE_THRESHOLD)
    brain = Brain()
    hud.boot(f"BRAIN · {config.MODEL}")
    try:
        from brain import memory, summaries
        hud.boot(f"MEMORY · {len(memory.store().all())} facts · {len(summaries.store().all())} conversations")
    except Exception:
        pass
    mic = Mic()
    hud.boot("MICROPHONE")

    timing = {}

    def listen(wait=config.WAIT_FOR_SPEECH_SECONDS):
        live = {"busy": False, "last": 0.0, "text": "", "voiced": 0, "level": 0.0}

        def on_frame(frames, level, speaking):
            hud.send(level=min(1.0, level / 2500))  # the orb's ring follows your voice
            live["level"] = level
            if level > getattr(mic, "last_threshold", config.SILENCE_RMS):
                live["voiced"] += 1
            # live words only after ~0.5 s of real voice (breaths and clicks make Whisper invent text)
            if not (config.LIVE_TRANSCRIPT and speaking and live["voiced"] >= 6):
                return
            now = time.time()
            if live["busy"] or now - live["last"] < 0.6 or len(frames) < 8:
                return
            live["busy"], live["last"] = True, now
            chunk = np.concatenate(frames).astype(np.float32) / 32768.0

            def work():  # live words while you're still talking
                txt = stt.partial(chunk)
                if txt:
                    live["text"] = txt
                    hud.send(user=txt)
                    line = f"  ... {txt[-60:]}   [mic {live['level']:.0f} / silence < {mic.last_threshold:.0f}]"
                    print("\r" + line.ljust(105)[:105], end="", flush=True)
                live["busy"] = False
            threading.Thread(target=work, daemon=True).start()

        audio = mic.record_command(
            config.SILENCE_RMS, config.SILENCE_SECONDS, config.MAX_RECORD_SECONDS, wait, on_frame=on_frame,
        )
        hud.send(level=0)
        if live["text"]:
            print()
        if audio is None:
            return ""
        got_it()  # instant feedback: you know it heard you before any processing starts
        t0 = time.time()
        # Tamil / Tanglish / English: local Whisper, or Gemini when Whisper wasn't sure (voice/language.py)
        text = language.hear(stt, audio)
        timing["hear"] = time.time() - t0
        if text:
            hud.send(user=text)
        return text

    def got_it():
        print("[got it]", flush=True)
        hud.state("thinking")
        threading.Thread(target=chime, args=(1320, 0.05, 0.12), daemon=True).start()  # tiny soft blip, non-blocking

    def say(text):
        hud.state("speaking")   # the words come sentence by sentence as she says them (tts.on_play)
        tts.say(text)

    def on_tool(name, args):
        log_tool(name, args)
        hud.tool(name, args)
        if name == "draft_email":   # "On it." right away, while the mail types itself (reel shot 2)
            threading.Thread(target=tts.say, args=("On it.",), daemon=True).start()

    def on_llm():
        if config.FILLERS:
            tts.filler()  # "let me think..." so the AI's thinking time doesn't feel dead

    def confirm(question):
        print(f"{config.ASSISTANT_NAME} (confirm): {question}")
        say(question)
        hud.state("listening")
        chime(660)
        mic.clear()
        answer = listen().lower()
        print(f"You: {answer}")
        return any(w in answer for w in YES_WORDS)

    def announce_due(then_idle=False):
        """Speak any reminders/timers that are due. Returns True if it spoke."""
        msgs = due_reminders()
        for msg in msgs:
            print(f"\n[reminder] {msg}")
            chime(990, 0.15)
            chime(990, 0.15)
            say(msg)
        if msgs:
            if then_idle:
                hud.state("idle")
            mic.clear()
            wake.reset()
        return bool(msgs)

    def ready():
        mic.clear()   # throw away whatever the mic heard while Jarvis was talking
        wake.reset()

    def conversation(first_command="", keep_audio=False):
        """One wake word, then keep going: after each reply Jarvis listens for a follow-up.
        first_command: what you said in the same breath as "Jarvis" ("Jarvis, open Chrome").
        keep_audio: don't throw away what the mic already caught. When Whisper recognised
        "Jarvis" it took ~1 s, and you may already be saying the command in that time.
        Returns False if the user asked Jarvis to shut down."""
        if first_command:  # "Jarvis, open Chrome" in one breath: straight to thinking
            hud.state("thinking")
            hud.send(user=first_command)
        else:
            hud.state("listening")
        if keep_audio:
            # Keep what you already started saying, but NOT the beep itself
            # (otherwise Jarvis hears its own beep as you talking and gives up).
            early = mic.drain()
            if not first_command:
                chime()
            mic.clear()
            mic.push_front(early)
        else:
            if not first_command:
                chime()
            mic.clear()
        wait = config.WAIT_FOR_SPEECH_SECONDS
        while True:
            if first_command:
                text, first_command = first_command, ""
            else:
                print("[listening]")
                deadline = time.time() + wait
                text = listen(wait)
                # A breath or room noise can start a "recording" that turns out empty.
                # Keep listening until the wait window is really over.
                while not text and time.time() < deadline - 0.5:
                    text = listen(deadline - time.time())
            if not text:
                print("[no follow-up, back to sleep]")
                hud.state("idle")
                return True
            print(f"You: {text}")
            lowered = text.lower()

            if any(p in lowered for p in STOP_PHRASES):
                say(line("offline"))
                hud.state("idle")
                return False
            mode = screen_mode(lowered)   # "big screen mode" / "back to normal": instant, no AI
            if mode:
                if hud.sock:
                    hud.send(mode=mode)
                    reply = line(f"screen_{mode}")
                else:
                    reply = line("screen_off")
                print(f"{config.ASSISTANT_NAME}: {reply}")
                say(reply)
                hud.state("listening")
                chime(660, 0.08, 0.15)
                mic.clear()
                wait = config.FOLLOW_UP_SECONDS
                continue
            if is_dismiss(lowered):
                say(line("dismiss"))
                hud.state("idle")
                return True

            print("[thinking]")
            start = time.time()
            live = {}   # chat replies: speak each sentence the moment the AI finishes writing it

            def on_sentence(sentence):
                if "speaker" not in live:
                    timing["think"] = time.time() - start   # until the first sentence was ready
                    live["speaker"], live["said"] = tts.stream(), sentence
                    hud.state("speaking")   # each sentence reaches the HUD when it's spoken (tts.on_play)
                else:
                    live["said"] += " " + sentence
                live["speaker"].add(sentence)

            try:
                with turn_lock:   # a phone command never runs the brain at the same time
                    reply = brain.ask(text, confirm, on_tool=on_tool, on_llm=on_llm, on_sentence=on_sentence)
            except Exception as e:  # Ollama stopped, out of VRAM, etc.
                print(f"Brain error: {e}")
                reply = line("brain_down")
                if "speaker" in live:   # it died mid-reply: let the started part finish, then say why
                    live.pop("speaker").finish()
            if "speaker" in live:
                print(f"{config.ASSISTANT_NAME}: {reply}")
                live["speaker"].finish()
            else:
                timing["think"] = time.time() - start
                print(f"{config.ASSISTANT_NAME}: {reply}")
                say(reply)
            total = config.SILENCE_SECONDS + timing.get("hear", 0) + timing["think"] + tts.first_audio
            print(f"   [timing] end-of-speech wait {config.SILENCE_SECONDS:.1f}s | hear {timing.get('hear', 0):.1f}s"
                  f" | think {timing['think']:.1f}s | voice starts {tts.first_audio:.1f}s"
                  f" | total {total:.1f}s")
            hud.send(stat=f"{total:.1f}s")   # the HUD's RESP readout: end of your speech -> Jarvis's voice
            if brain.ai_timing():
                print(f"   [timing] {brain.ai_timing()}")

            announce_due()
            # Follow-up window: soft low chime, then listen again without the wake word
            hud.state("listening")
            mic.clear()
            chime(660, 0.08, 0.15)
            mic.clear()
            wait = config.FOLLOW_UP_SECONDS

    # Backup trigger: press Enter in this window to wake Jarvis (handy when filming)
    enter = threading.Event()

    def enter_listener():
        while True:
            try:
                input()
            except EOFError:
                return
            enter.set()

    threading.Thread(target=enter_listener, daemon=True).start()

    turn_lock = threading.Lock()

    def remote_run(text, ask_phone, speak, phone_card=None):
        """A command from his phone (Jarvis Remote): same brain, questions go back to the phone."""
        def tool(name, args):   # action tags on both HUDs: the PC's and the phone's (the phone says "On it.")
            log_tool(name, args)
            hud.tool(name, args)
            if phone_card:
                phone_card(name, args)
        print(f"\n[phone] You: {text}")
        if not turn_lock.acquire(timeout=30):
            return "I'm in the middle of something on the PC. Try again in a moment."
        try:
            hud.state("thinking")
            hud.send(user=f"📱 {text}")
            try:
                reply = brain.ask(text, ask_phone, on_tool=tool)
            except Exception as e:
                print(f"Brain error: {e}")
                reply = line("brain_down")
            print(f"[phone] {config.ASSISTANT_NAME}: {reply}")
            if speak or config.REMOTE_SPEAK:
                say(reply)
            else:
                hud.send(reply=reply)
            hud.state("idle")
            return reply
        finally:
            turn_lock.release()

    start_remote(remote_run, stt)   # with Whisper + voice: he can TALK to Jarvis on the phone
    mic.start()
    hud.state("wake")
    from brain.persona import startup_greeting
    from tools.extras import today_reminders
    greeting = startup_greeting(today_reminders())   # "Good evening, sir. Jarvis online. One reminder today: ..."
    print(f"{config.ASSISTANT_NAME}: {greeting}")
    say(greeting)
    hud.state("idle")
    ready()

    summarizing = threading.Event()   # a summary is being written right now
    summaries_due = [time.time() + 20]

    def summarize_in_background():
        try:
            n, secs = brain.summarize_pending()
            if n:
                print(f"\n   [memory] summarised {n} finished conversation{'s' * (n > 1)} in {secs:.1f}s")
        finally:
            summarizing.clear()

    use_model = config.WAKE_MODE in ("model", "both")
    use_whisper = config.WAKE_MODE in ("whisper", "both")
    # frames of 80 ms: 0.4 s before, 0.5 s quiet ends it, 12 s max (a one-breath "Keena, mail my CTO... ask how..." is ~8 s)
    PRE_ROLL, END_QUIET, MAX_SEG = 5, 6, 150

    while True:
        print(f'\nWaiting for "{config.ASSISTANT_NAME}"...  (or press Enter)')
        enter.clear()
        best_score, loudest, frames, got_audio = 0.0, 0, 0, False
        preroll, segment, quiet, last_heard = [], [], 0, ""
        command, by_whisper = "", False
        next_check = 0.0
        while True:
            if time.time() >= next_check:  # reminders: check about once a second
                next_check = time.time() + 1
                if announce_due(then_idle=True):
                    print(f'\nWaiting for "{config.ASSISTANT_NAME}"...  (or press Enter)')
                if time.time() >= summaries_due[0] and not summarizing.is_set():
                    summaries_due[0] = time.time() + 60   # finished conversations -> summaries, while idle
                    summarizing.set()
                    threading.Thread(target=summarize_in_background, daemon=True).start()
            frame = mic.read(timeout=0.25)
            if enter.is_set():
                print("\r" + " " * 100 + "\r[woken by Enter]")
                break
            if frame is None:
                if not got_audio:
                    print("\r  !! no audio arriving from the mic: check Windows mic settings / MIC_DEVICE", end="", flush=True)
                continue
            got_audio = True
            level = rms(frame)

            # 1) openWakeWord "hey jarvis" model
            if use_model:
                s = wake.score(frame)
                best_score = max(best_score, s)
                if s >= config.WAKE_THRESHOLD:
                    print("\r" + " " * 100 + "\r[wake word heard]")
                    break

            # 2) Whisper: cut speech into short chunks and look for the word "Jarvis"
            if use_whisper:
                if segment:
                    segment.append(frame)
                    quiet = 0 if level > config.SILENCE_RMS else quiet + 1
                    if quiet >= END_QUIET or len(segment) >= MAX_SEG:
                        t0 = time.time()
                        clip = np.concatenate(segment).astype(np.float32) / 32768.0
                        heard = stt.transcribe(clip, strip_wake=False) if len(segment) - quiet >= 3 else ""
                        timing["hear"] = time.time() - t0
                        segment, quiet = [], 0
                        if heard:
                            last_heard = heard
                            found = find_wake(heard)
                            if found is not None:
                                print("\r" + " " * 100 + f"\r[heard \"{config.ASSISTANT_NAME}\"]  ({heard})")
                                if found:
                                    got_it()  # "Jarvis, open Chrome" in one breath: confirm right away
                                    # "Keena, naan Maggi pannitu irukken..." in one breath: the command may be Tamil
                                    if language.needs_gemini(found, stt.last_confidence):
                                        english = language.understand(language.wav_bytes(clip))
                                        found = strip_wake(english) if english else found
                                command, by_whisper = found, True
                                break
                elif level > config.SILENCE_RMS:
                    segment = preroll + [frame]
                preroll = (preroll + [frame])[-PRE_ROLL:]

            loudest, frames = max(loudest, int(level)), frames + 1
            if frames >= 25:  # live status every 2 seconds, on one line
                status = f"\r  mic {loudest:5d}"
                if use_model:
                    status += f" | wake score {best_score:.2f}/{config.WAKE_THRESHOLD}"
                if use_whisper and last_heard:
                    status += f" | heard: {last_heard[:40]!r}"
                status += f" | room noise {mic.noise_floor():.0f} | lag {mic.lag():.1f}s"
                print("\r" + status.lstrip("\r")[:110].ljust(110), end="", flush=True)
                best_score, loudest, frames = 0.0, 0, 0

        if not conversation(command, keep_audio=by_whisper):
            break
        ready()


if __name__ == "__main__":
    try:
        if "--text" in sys.argv:
            text_mode()
        elif "--mic-test" in sys.argv:
            mic_test()
        elif "--brain-test" in sys.argv:
            Brain().benchmark()
        else:
            voice_mode()
    except KeyboardInterrupt:
        print(f"\n{config.ASSISTANT_NAME} stopped.")
