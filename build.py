#!/usr/bin/env python3
"""Build PS3 Rich Presence as a macOS .app or Windows .exe.

Usage (macOS):
    uv run --script build.py

Usage (Windows):
    uv run --script build.py

Requires: PyInstaller (installed automatically by uv from the deps below).
"""
# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "pyinstaller",
#     "Pillow",
# ]
# ///

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIST = HERE / "dist"
BUILD = HERE / "build"
ICON_SRC = HERE / "img" / "ps32025.png"
ICON_ICO = BUILD / "icon.ico"
ICON_ICNS = BUILD / "icon.icns"


def make_icon():
    """Generate platform-appropriate icon from the project PNG."""
    from PIL import Image

    BUILD.mkdir(exist_ok=True)

    # Load and make a square icon (pad the banner into a square)
    img = Image.open(ICON_SRC).convert("RGBA")
    # Create a square canvas
    size = max(img.width, img.height)
    square = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    x = (size - img.width) // 2
    y = (size - img.height) // 2
    square.paste(img, (x, y))

    if platform.system() == "Darwin":
        # macOS needs .icns - generate from multiple sizes
        icon_sizes = [16, 32, 64, 128, 256, 512]
        iconset = BUILD / "icon.iconset"
        iconset.mkdir(exist_ok=True)
        for s in icon_sizes:
            resized = square.resize((s, s), Image.LANCZOS)
            resized.save(iconset / f"icon_{s}x{s}.png")
            # Retina
            s2 = s * 2
            if s2 <= 1024:
                resized2 = square.resize((s2, s2), Image.LANCZOS)
                resized2.save(iconset / f"icon_{s}x{s}@2x.png")
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(ICON_ICNS)],
            check=True,
        )
        print(f"Created {ICON_ICNS}")
        return str(ICON_ICNS)
    else:
        # Windows .ico
        square_256 = square.resize((256, 256), Image.LANCZOS)
        square_256.save(
            ICON_ICO,
            format="ICO",
            sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
        )
        print(f"Created {ICON_ICO}")
        return str(ICON_ICO)


def build():
    icon_path = make_icon()

    # Collect data files
    data_args = []
    img_dir = HERE / "img"
    if img_dir.is_dir():
        sep = ";" if platform.system() == "Windows" else ":"
        data_args.extend(["--add-data", f"{img_dir}{sep}img"])

    # Include PS3RPD.py as a data file so gui.py can import it
    data_args.extend([
        "--add-data",
        f"{HERE / 'PS3RPD.py'}{';' if platform.system() == 'Windows' else ':'}.",
    ])

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",  # no console window
        "--onedir",
        "--name", "PS3 Rich Presence",
        f"--icon={icon_path}",
        "--distpath", str(DIST),
        "--workpath", str(BUILD),
        *data_args,
        # Hidden imports that PyInstaller may miss
        "--hidden-import", "pypresence",
        "--hidden-import", "networkscan",
        "--hidden-import", "bs4",
        "--hidden-import", "requests",
        "--hidden-import", "PIL",
        "--hidden-import", "PIL.Image",
        "--hidden-import", "PIL.ImageTk",
        str(HERE / "gui.py"),
    ]

    print(f"\nRunning: {' '.join(cmd)}\n")
    subprocess.run(cmd, check=True)

    if platform.system() == "Darwin":
        app_path = DIST / "PS3 Rich Presence.app"
        if app_path.exists():
            print(f"\n{'=' * 60}")
            print(f"  macOS app built: {app_path}")
            print(f"  To run: open '{app_path}'")
            print(f"  To distribute: zip it or drag to /Applications")
            print(f"{'=' * 60}")
    else:
        exe_path = DIST / "PS3 Rich Presence" / "PS3 Rich Presence.exe"
        if exe_path.exists():
            print(f"\n{'=' * 60}")
            print(f"  Windows exe built: {exe_path}")
            print(f"{'=' * 60}")


if __name__ == "__main__":
    build()
