"""Rebuilds the README examples from assets/examples/src with fixed boxes and seeds.

Run from the repo root (model mode needs rembg):
    python tools/make_examples.py
Writes assets/examples/out/*.png and assets/showcase.png.
"""
import os
import sys
import time

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sticker_kit as sk  # noqa: E402

SRC = "assets/examples/src"
OUT = "assets/examples/out"

RODENTS = {
    "frank": [(330, 300), (400, 370), (300, 200), (420, 120), (230, 240), (520, 320)],
    "rinky": [(700, 200), (680, 300), (670, 400), (600, 235), (800, 250), (715, 415),
              (755, 430), (770, 448), (745, 415), (700, 380)],
    "gimera": [(830, 420), (800, 500), (900, 470), (920, 350), (815, 300)],
}
WITCH = {
    "pepper": [(500, 250), (460, 120), (520, 700), (450, 950), (730, 340), (430, 450), (600, 500),
               (380, 880), (560, 1040), (420, 650), (470, 800), (560, 640), (600, 850), (420, 1000),
               (500, 1000), (600, 1000), (520, 560), (610, 720), (585, 600), (600, 690), (560, 760),
               (605, 800), (540, 600), (575, 560)],
    "carrot": [(770, 600), (700, 850), (1060, 480), (920, 800), (680, 680), (760, 1040), (860, 520),
               (700, 760), (780, 900), (720, 1000), (800, 760), (660, 960)],
}

# (row label, source file, [(output name, box, seeds, keep)])
ROWS = [
    ("Illustration", "illustration.jpg", [
        ("illustration_group", (330, 0, 1280, 1280), WITCH, None),
    ]),
    ("3D animation", "3d.jpg", [
        ("3d_group", (180, 40, 1060, 667), RODENTS, None),
        ("3d_frank", (180, 40, 1060, 667), RODENTS, ["frank"]),
        ("3d_rinky", (180, 40, 1060, 667), RODENTS, ["rinky"]),
        ("3d_gimera", (180, 40, 1060, 667), RODENTS, ["gimera"]),
    ]),
    ("Person", "person.jpg", [("person", (80, 440, 1080, 1340), None, None)]),
    ("Animal", "animal.jpg", [("animal", (0, 270, 1010, 853), None, None)]),
    ("Object", "object.jpg", [("object", (40, 220, 1160, 1490), None, None)]),
]


def font(size):
    for name in ("DejaVuSans-Bold.ttf", "arialbd.ttf", "LiberationSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def main():
    os.makedirs(OUT, exist_ok=True)
    rows = []
    for label, src, cuts in ROWS:
        F = sk.load(os.path.join(SRC, src))
        made = []
        for name, box, seeds, keep in cuts:
            t = time.time()
            st = sk.make(F, box, seeds=seeds, keep=keep)
            st.save(os.path.join(OUT, name + ".png"))
            size, _ = sk.save_webp(st, os.path.join(OUT, name + ".webp"))
            print("%-14s %5.1f s  %6d bytes" % (name, time.time() - t, size))
            made.append(st)
        rows.append((label, Image.open(os.path.join(SRC, src)).convert("RGB"), made))

    T, pad, gap, lw = 220, 28, 14, 330
    width = pad + lw + 40 + 4 * (T + gap) + pad
    height = pad + 30 + len(rows) * (T + gap) + pad
    canvas = Image.new("RGB", (width, height), (250, 249, 246))
    d = ImageDraw.Draw(canvas)
    d.text((pad, pad), "input", font=font(18), fill=(110, 110, 110))
    d.text((pad + lw + 40, pad), "stickers (512 px WebP, white outline)", font=font(18), fill=(110, 110, 110))
    y = pad + 30
    for label, src_img, made in rows:
        th = src_img.copy()
        th.thumbnail((lw, T))
        canvas.paste(th, (pad, y + (T - th.height) // 2))
        d.rounded_rectangle([pad + 6, y + 6, pad + 6 + 12 * len(label) + 16, y + 32], radius=8, fill=(30, 30, 36))
        d.text((pad + 14, y + 10), label, font=font(15), fill=(255, 255, 255))
        ax, ay = pad + lw + 10, y + T // 2
        d.polygon([(ax, ay - 10), (ax + 22, ay), (ax, ay + 10)], fill=(160, 160, 160))
        for i, st in enumerate(made):
            tile = Image.new("RGBA", (T, T), (44, 48, 58, 255))
            s = st.copy()
            s.thumbnail((T - 12, T - 12))
            tile.alpha_composite(s, ((T - s.width) // 2, (T - s.height) // 2))
            canvas.paste(tile.convert("RGB"), (pad + lw + 40 + i * (T + gap), y))
        y += T + gap
    canvas.save("assets/showcase.png")
    print("wrote assets/showcase.png")


if __name__ == "__main__":
    main()
