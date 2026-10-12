"""Fails if a tracked text file leaks personal data or breaks the repo text rules.

Checks: email addresses (except GitHub noreply), local user paths, non-ASCII
characters, em dashes, and any word listed in a local `.pii-words` file (one per
line, never committed). Run from the repo root before every commit:
    python tools/check_pii.py
"""
import os
import re
import subprocess
import sys

TEXT_EXT = {".py", ".md", ".txt", ".yml", ".yaml", ".json", ".toml", ".cfg", ""}
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
LOCAL_PATH = re.compile(r"([A-Za-z]:[\\/]+Users[\\/]+\w+)|(/home/\w+)|(/Users/\w+)")
SELF = "tools/check_pii.py"


def tracked_files():
    try:
        out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True).stdout
        files = out.split()
    except (OSError, subprocess.CalledProcessError):
        files = []
    if not files:  # not a repo yet: scan the working tree
        for root, dirs, names in os.walk("."):
            dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", ".venv", "out")]
            files += [os.path.relpath(os.path.join(root, n)).replace(os.sep, "/") for n in names]
    return [f for f in files if os.path.splitext(f)[1].lower() in TEXT_EXT and f != ".pii-words"]


def main():
    words = []
    if os.path.exists(".pii-words"):
        words = [w.strip() for w in open(".pii-words", encoding="utf8") if w.strip()]
    problems = []
    for path in tracked_files():
        try:
            text = open(path, encoding="utf8").read()
        except (UnicodeDecodeError, OSError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            where = "%s:%d" % (path, n)
            for m in EMAIL.findall(line):
                if not m.endswith("@users.noreply.github.com"):
                    problems.append("%s email %s" % (where, m))
            if path != SELF and LOCAL_PATH.search(line):
                problems.append("%s local path" % where)
            if any(ord(c) > 127 for c in line):
                problems.append("%s non-ASCII character" % where)
            low = line.lower()
            for w in words:
                if w.lower() in low:
                    problems.append("%s personal word from .pii-words" % where)
    for p in problems:
        print(p)
    print("check_pii:", "%d problem(s)" % len(problems) if problems else "clean",
          "(%d personal words checked)" % len(words))
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
