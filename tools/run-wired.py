"""Run the real daemon in wired mode for a while and log everything, so we can see what the
host actually sends.

    python3 /home/deck/run-wired.py steam_controller 240 /tmp/steam.log

Starts the daemon, runs it for the given seconds, then stops it. Uses the installed plugin, so
it exercises the shipped code rather than a copy.
"""
import subprocess
import sys
import threading

PLUGIN = "/home/deck/homebrew/plugins/deck-as-controller"


def main():
    profile = sys.argv[1] if len(sys.argv) > 1 else "steam_controller"
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 120.0
    logfile = sys.argv[3] if len(sys.argv) > 3 else "/tmp/sdcd-wire.log"

    options = '{"profile":"%s","connection":"usb"}' % profile
    with open(logfile, "w") as log:
        proc = subprocess.Popen(
            ["/usr/bin/timeout", str(seconds + 5), "/usr/bin/python3", "-m", "sdcd",
             "--settings-dir", "/tmp/sdcd-wire", "--options", options],
            cwd=PLUGIN, env={"PYTHONPATH": PLUGIN + "/py_modules",
                             "PYTHONUNBUFFERED": "1", "HOME": "/home/deck",
                             "PATH": "/usr/bin:/bin"},
            stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT, text=True,
            bufsize=1)

        def stop():
            try:
                proc.stdin.write('{"cmd":"stop"}\n')
                proc.stdin.flush()
            except (OSError, ValueError):
                pass

        threading.Timer(seconds, stop).start()
        try:
            proc.wait(timeout=seconds + 15)
        except subprocess.TimeoutExpired:
            proc.kill()
    print("wrote %s" % logfile)


if __name__ == "__main__":
    main()