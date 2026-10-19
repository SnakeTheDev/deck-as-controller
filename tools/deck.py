"""Run a command on the Deck over SSH. The plugin's Deck, reached from the hotspot.

    python tools/deck.py "uname -r" "ls /sys/kernel/config/usb_gadget"
    python tools/deck.py --sudo "cat /sys/class/udc/*/state"
"""
import getpass
import os
import shlex
import sys
import time

import paramiko

HOST = os.environ.get("DECK_HOST", "192.168.137.101")
USER = os.environ.get("DECK_USER", "deck")
PASSWORD = os.environ.get("DECK_PASSWORD", "root")


def connect():
    """The Deck roams between access points, so a connect can fail while it is fine."""
    last = None
    for attempt in range(5):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(HOST, username=USER, password=PASSWORD, timeout=20,
                           banner_timeout=30, auth_timeout=30,
                           allow_agent=False, look_for_keys=False)
            return client
        except Exception as e:  # the Deck's Wi-Fi drops for a second or two now and then
            last = e
            client.close()
            time.sleep(3)
    raise last


def put(local: str, remote: str):
    client = connect()
    try:
        sftp = client.open_sftp()
        with open(local, "rb") as f:
            sftp.putfo(f, remote)
        sftp.chmod(remote, 0o755)
        print(f"sent {local} -> {remote}")
    finally:
        client.close()


def run(commands, sudo=False):
    client = connect()
    try:
        for command in commands:
            # Through a shell so builtins, pipes and redirects work under sudo.
            full = (f"echo {PASSWORD} | sudo -S sh -c {shlex.quote(command)}" if sudo
                    else f"sh -c {shlex.quote(command)}")
            _, out, err = client.exec_command(full, timeout=120)
            status = out.channel.recv_exit_status()
            text = out.read().decode(errors="replace")
            error = err.read().decode(errors="replace").replace(f"{PASSWORD}\r\n", "")
            print(f"$ {command}\n{text}", end="")
            if error.strip():
                print(error, end="", file=sys.stderr)
            print(f"[exit {status}]\n")
    finally:
        client.close()


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--put":
        put(args[1], args[2])
        sys.exit()
    as_root = "--sudo" in args
    run([a for a in args if a != "--sudo"], sudo=as_root)