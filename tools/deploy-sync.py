"""Copy the working tree's daemon onto the Deck's installed plugin, and prove it landed.

    python tools/deploy-sync.py

Puts each file in place over SFTP, compares the bytes read back against the ones sent, and
cleans up the staging copies. No shell quoting involved, which is the point: three layers of
PowerShell, paramiko and sudo is where a diagnosis like the wired one goes wrong.
"""
import hashlib
import os
import posixpath
import sys

import paramiko

HOST = os.environ.get("DECK_HOST", "192.168.137.101")
USER = os.environ.get("DECK_USER", "deck")
PASSWORD = os.environ.get("DECK_PASSWORD", "root")
PLUGIN = "/home/deck/homebrew/plugins/deck-as-controller"
STAGE = "/home/deck/sdcd-stage"

# (local path relative to the repo, path under the plugin directory)
FILES = [
    ("main.py", "main.py"),
    ("dist/index.js", "dist/index.js"),
    ("py_modules/sdcd/usb.py", "py_modules/sdcd/usb.py"),
    ("py_modules/sdcd/daemon.py", "py_modules/sdcd/daemon.py"),
    ("py_modules/sdcd/profiles/xbox.py", "py_modules/sdcd/profiles/xbox.py"),
    ("py_modules/sdcd/profiles/steam.py", "py_modules/sdcd/profiles/steam.py"),
    ("py_modules/sdcd/profiles/base.py", "py_modules/sdcd/profiles/base.py"),
]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASSWORD, timeout=15,
                   allow_agent=False, look_for_keys=False)
    sftp = client.open_sftp()
    failed = False
    try:
        for local, relative in FILES:
            with open(os.path.join(root, local), "rb") as f:
                wanted = f.read()
            staged = posixpath.join(STAGE, relative)
            # The plugin directory is root owned, so stage as deck then move with sudo.
            for directory in sorted({posixpath.dirname(staged)[:i + 1]
                                     for i in range(len(staged)) if "/" in staged[:i + 1]}):
                try:
                    sftp.mkdir(directory)
                except IOError:
                    pass
            with sftp.open(staged, "wb") as f:
                f.write(wanted)
            ok = False
            for command in (f"sudo -S cp {staged} {PLUGIN}/{relative}",
                            f"sudo -S rm -f {staged}"):
                _, out, _ = client.exec_command(f"echo {PASSWORD} | {command}")
                ok = out.channel.recv_exit_status() == 0
            try:
                with sftp.open(f"{PLUGIN}/{relative}", "rb") as f:
                    landed = f.read()
            except IOError:
                landed = b""
            match = digest(landed) == digest(wanted)
            failed |= not match
            print(f"  {'ok  ' if ok and match else 'FAIL'} {relative:<40} {digest(wanted)}"
                  f"{'' if match else ' != ' + digest(landed)}")
        print("all in sync" if not failed else "SOME FILES DID NOT LAND")
    finally:
        client.exec_command(f"echo {PASSWORD} | sudo -S rm -rf {STAGE}")
        sftp.close()
        client.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())