"""python -m remote.pair : shows the QR code that pairs his phone with Jarvis Remote.

Scan it with the phone camera; the page opens and remembers the key. Then "Add to Home screen".
python -m remote.pair --new : make a NEW key (every paired phone has to scan again).
"""
import os
import sys

import config
from remote import server


def show(title, url, qrcode):
    print(f"\n=== {title} ===\n{url}\n")
    if qrcode:
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.print_ascii(invert=True)


def main():
    if "--new" in sys.argv and os.path.exists(server.KEY_FILE):
        os.remove(server.KEY_FILE)
        print("Old key removed: phones paired before must scan again.\n")
    try:
        import qrcode
    except ImportError:
        qrcode = None
    https = config.REMOTE_HTTPS_PORT or None
    if https:
        server.certificate()   # make sure the certificate covers today's addresses
    ts = server.tailscale_https()
    if ts:   # a real certificate through Tailscale: no warning at all
        show(f"{config.ASSISTANT_NAME}, anywhere (Tailscale)", f"{ts}/#k={server.key()}", qrcode)
    found = server.urls(config.REMOTE_PORT, https)
    if not config.REMOTE_LAN:
        found = [(n, u) for n, u in found if "Tailscale" in n]
    if not found and not ts:
        print("No network address found. Connect to Wi-Fi, or sign in to Tailscale (works from anywhere).")
        return
    for name, url in found:
        show(f"{config.ASSISTANT_NAME}, {name}", url, qrcode)
    if https:
        print("FIRST TIME ON THE PHONE: Chrome says \"Your connection is not private\". That's expected: the PC made its")
        print("own certificate (no company signed it). The connection is still encrypted. Tap  Advanced  ->")
        print("Proceed to ... (unsafe)  once. Then allow the microphone, and in Chrome's menu tap  Add to Home screen.")
    print("\nKeep this code private: anyone with it (on your network) can control her. "
          "python -m remote.pair --new makes a new one.")


if __name__ == "__main__":
    main()
