"""Une los fotogramas de frontend/e2e/demo-gif.spec.ts en docs/demo.gif.

Uso (Pillow no es dependencia del proyecto; se pide solo para esta herramienta):
    uv run --with pillow scripts/make-demo-gif.py DIRECTORIO_FOTOGRAMAS docs/demo.gif
"""

import json
import sys
from pathlib import Path

from PIL import Image

WIDTH = 960  # legible en el README y de peso razonable


def main(frames_dir: Path, out: Path) -> None:
    frames = json.loads((frames_dir / "frames.json").read_text(encoding="utf-8"))
    images = []
    for item in frames:
        img = Image.open(frames_dir / item["file"]).convert("RGB")
        height = round(img.height * WIDTH / img.width)
        img = img.resize((WIDTH, height), Image.LANCZOS)
        images.append(
            img.quantize(colors=128, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        )
    images[0].save(
        out,
        save_all=True,
        append_images=images[1:],
        duration=[item["ms"] for item in frames],
        loop=0,
        optimize=True,
    )
    print(f"{out} · {len(images)} fotogramas · {out.stat().st_size / 1024:.0f} KiB")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(Path(sys.argv[1]), Path(sys.argv[2]))
