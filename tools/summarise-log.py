"""Summarise what the host sent us, from the plugin's own log.

    python3 /home/deck/summarise-log.py [logfile]

Picks the newest deck-as-controller log if none is given.
"""
import collections
import glob
import re
import sys

LOG_DIR = "/home/deck/homebrew/logs/deck-as-controller"


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else max(glob.glob(f"{LOG_DIR}/*.log"), key=__import__("os").path.getmtime)
    print(f"log: {path}")
    lines = open(path, errors="replace").read().splitlines()

    sent = collections.Counter()
    for line in lines:
        m = re.search(r"host report (0x[0-9a-f]+), \d+ bytes: ((?:[0-9a-f]{2} )+)", line)
        if m:
            sent[(m.group(1), " ".join(m.group(2).split()[:6]))] += 1
    print(f"\nreports the host sent us ({sum(sent.values())} total):")
    for (report_id, head), count in sent.most_common(12):
        print(f"  {count:>4}x {report_id}  {head}")

    answers = [line for line in lines if "answered GET_REPORT" in line]
    print(f"\nGET_REPORT answers: {len(answers)}")
    for line in answers[:3]:
        print("  " + line.split("sdcd.usb:")[-1].strip()[:110])
    repeats = [line for line in lines if "answered GET_REPORT again" in line]
    print(f"  of which repeats (our answer did not clear the request): {len(repeats)}")

    warnings = [line for line in lines
                if "WARNING" in line or "GET_REPORT:" in line or "rx_errors" in line]
    print(f"\nwarnings: {len(warnings)}")
    for line in warnings[:5]:
        print("  " + line.strip()[:140])


if __name__ == "__main__":
    main()