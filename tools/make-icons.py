#!/usr/bin/env python3
"""Generate the hicolor icon set from krbctl.mascot.

    python3 tools/make-icons.py

The artwork is cairo code, so this needs no source image — it can be re-run at
any time to pick up a change to the mascot. The output PNGs are committed so
that installing never depends on cairo being importable with the right version.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from krbctl import icons  # noqa: E402

SIZES = (16, 22, 24, 32, 48, 64, 128, 256, 512)


def main() -> int:
    for size in SIZES:
        target = ROOT / "data" / "icons" / "hicolor" / f"{size}x{size}" / "apps" / "krbctl.png"
        icons.write_png(str(target), size)
        print(f"wrote {target.relative_to(ROOT)}")

    # A tray-sized monochrome face for icon themes that look one up by name.
    symbolic = ROOT / "data" / "icons" / "hicolor" / "22x22" / "apps" / "krbctl-symbolic.png"
    icons.write_png(str(symbolic), 22, "none", app_icon=False)
    print(f"wrote {symbolic.relative_to(ROOT)}")

    preview = ROOT / "data" / "icons" / "preview-states.png"
    icons.preview_sheet(str(preview))
    print(f"wrote {preview.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
