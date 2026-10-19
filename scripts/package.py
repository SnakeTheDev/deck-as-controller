"""Build out/deck-as-controller.zip: the same staging as scripts/package.sh, in Python.

scripts/package.sh is the one to run on the Deck or any Linux box. This is here so the
plugin zip can also be built on Windows or macOS, where rsync and zip may not be there.
Both must produce a zip holding exactly:

    deck-as-controller/{main.py,plugin.json,package.json,README.md,dist/index.js,py_modules/}

Decky installs it by extracting deck-as-controller/ over the plugin directory.
"""
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "deck-as-controller"
FILES = ("main.py", "plugin.json", "package.json", "README.md")
# A fixed timestamp so the zip only changes when its contents do.
TIMESTAMP = (2026, 1, 1, 0, 0, 0)


def build_frontend():
    """pnpm build, on Windows too (where pnpm is a .cmd shim)."""
    pnpm = shutil.which("pnpm") or shutil.which("pnpm.cmd")
    subprocess.run([pnpm or "pnpm", "build"], cwd=ROOT, check=True, shell=not pnpm)


def stage(destination: Path):
    (destination / "dist").mkdir(parents=True)
    shutil.copy2(ROOT / "dist/index.js", destination / "dist/index.js")
    for name in FILES:
        shutil.copy2(ROOT / name, destination / name)
    for source in sorted((ROOT / "py_modules").rglob("*")):
        if "__pycache__" in source.parts:
            continue
        target = destination / source.relative_to(ROOT)
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def main():
    if "--no-build" not in sys.argv:
        build_frontend()
    out = ROOT / "out" / f"{NAME}.zip"
    out.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp) / NAME
        stage(staged)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            for source in sorted(p for p in staged.rglob("*") if p.is_file()):
                entry = zipfile.ZipInfo(source.relative_to(staged.parent).as_posix(),
                                        date_time=TIMESTAMP)
                entry.external_attr = 0o644 << 16
                archive.writestr(entry, source.read_bytes())
    print(out.relative_to(ROOT).as_posix())


if __name__ == "__main__":
    main()