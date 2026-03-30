#!/usr/bin/env python3
"""Generate crisp Windows/Tauri icon assets from the desktop logo.

The in-app logo can keep a little breathing room, but the Windows shell icon
needs hand-sized small layers so taskbar/titlebar rendering does not blur or
shrink the two figures into mush.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_LOGO = REPO_ROOT / "desktop_app/src/assets/logo.png"
ICON_DIR = REPO_ROOT / "desktop_app/src-tauri/icons"

WINDOWS_ICON_SIZES = [32, 16, 20, 24, 40, 48, 64, 128, 256]
PNG_ICON_SIZES = {
    "32x32.png": 32,
    "128x128.png": 128,
    "128x128@2x.png": 256,
    "icon.png": 512,
}
APPX_SIZES = {
    "Square30x30Logo.png": 30,
    "Square44x44Logo.png": 44,
    "Square71x71Logo.png": 71,
    "Square89x89Logo.png": 89,
    "Square107x107Logo.png": 107,
    "Square142x142Logo.png": 142,
    "Square150x150Logo.png": 150,
    "Square284x284Logo.png": 284,
    "Square310x310Logo.png": 310,
    "StoreLogo.png": 50,
}


def figure_bbox(image: Image.Image) -> tuple[int, int, int, int]:
    rgba = image.convert("RGBA")
    mask = Image.new("L", rgba.size, 0)
    pixels = rgba.load()
    mask_pixels = mask.load()
    for y in range(rgba.height):
        for x in range(rgba.width):
            r, g, b, a = pixels[x, y]
            if a > 8 and max(r, g, b) > 36:
                mask_pixels[x, y] = 255
    bbox = mask.getbbox()
    if bbox is None:
        raise RuntimeError("failed to detect logo foreground")
    return bbox


def render_icon(size: int, figure: Image.Image) -> Image.Image:
    # Smaller shell sizes need tighter padding so the figures remain legible.
    scale_by_size = {
        16: 0.80,
        24: 0.78,
        20: 0.79,
        30: 0.77,
        32: 0.76,
        40: 0.74,
        44: 0.74,
        48: 0.73,
        50: 0.72,
        64: 0.70,
        71: 0.70,
        89: 0.69,
        107: 0.68,
        128: 0.68,
        142: 0.67,
        150: 0.67,
        256: 0.66,
        284: 0.66,
        310: 0.66,
        512: 0.66,
    }
    figure_scale = scale_by_size.get(size, 0.68)
    figure_height = max(8, round(size * figure_scale))
    figure_width = max(8, round(figure.width * (figure_height / figure.height)))
    foreground = figure.resize((figure_width, figure_height), Image.Resampling.NEAREST)

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    radius = max(3, round(size * 0.24))
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=(0, 0, 0, 255))

    x = (size - figure_width) // 2
    y = (size - figure_height) // 2
    if size <= 24:
        y -= 1
    canvas.alpha_composite(foreground, (x, y))
    return canvas


def main() -> None:
    source = Image.open(SOURCE_LOGO).convert("RGBA")
    bbox = figure_bbox(source)
    # Keep the figures only; rebuild the black square per target size.
    figure = source.crop(bbox)

    generated: dict[int, Image.Image] = {}
    for size in sorted(set(WINDOWS_ICON_SIZES) | set(PNG_ICON_SIZES.values()) | set(APPX_SIZES.values())):
        generated[size] = render_icon(size, figure)

    ICON_DIR.mkdir(parents=True, exist_ok=True)

    for filename, size in PNG_ICON_SIZES.items():
        generated[size].save(ICON_DIR / filename)

    for filename, size in APPX_SIZES.items():
        generated[size].save(ICON_DIR / filename)

    generated[256].save(
        ICON_DIR / "icon.ico",
        format="ICO",
        sizes=[(size, size) for size in WINDOWS_ICON_SIZES],
    )

    print(f"wrote icons to {ICON_DIR}")


if __name__ == "__main__":
    main()
