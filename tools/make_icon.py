"""生成程序图标（开发用）：assets/icon.ico / assets/icon.png。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"


def make_image(size: int = 256) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    radius = int(size * 0.22)
    # 深蓝底
    draw.rounded_rectangle((2, 2, size - 2, size - 2), radius=radius, fill=(31, 111, 235, 255))
    # 内圈高光
    draw.rounded_rectangle((int(size * 0.10), int(size * 0.10), int(size * 0.90), int(size * 0.90)),
                           radius=int(size * 0.16), outline=(255, 255, 255, 60), width=max(2, size // 64))
    # 字母 C
    try:
        font = ImageFont.truetype("arialbd.ttf", int(size * 0.56))
    except OSError:
        font = ImageFont.load_default()
    text = "C"
    bbox = draw.textbbox((0, 0), text, font=font)
    x = (size - (bbox[2] - bbox[0])) / 2 - bbox[0]
    y = (size - (bbox[3] - bbox[1])) / 2 - bbox[1]
    draw.text((x, y), text, font=font, fill=(255, 255, 255, 255))
    # 右下角小点（提示词）
    dot = int(size * 0.10)
    draw.ellipse((size - dot - int(size * 0.12), size - dot - int(size * 0.12),
                  size - int(size * 0.12), size - int(size * 0.12)), fill=(255, 214, 102, 255))
    return image


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    image = make_image(256)
    image.save(ASSETS / "icon.png")
    image.save(ASSETS / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("saved", ASSETS / "icon.ico", ASSETS / "icon.png")


if __name__ == "__main__":
    main()
