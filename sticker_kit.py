#!/usr/bin/env python3
"""sticker_kit: cut characters out of screenshots (no image generation) into WhatsApp stickers.

One file. Run `python sticker_kit.py guide` for instructions and
`python sticker_kit.py selftest` to check the file and its dependencies.

Copyright (C) 2026 filep48 (https://github.com/filep48)
Licensed under the GNU AGPL v3 with an additional term under section 7(b):
keep, and show in the Appropriate Legal Notices of any work that contains this
program (including user interfaces and web pages), the attribution
    Made with sticker-kit by filep48 (https://github.com/filep48/sticker-kit)
Full text: https://github.com/filep48/sticker-kit (LICENSE and NOTICE).
Commercial license for closed-source use: open an issue in that repository.
"""
import argparse
import io
import json
import os
import sys
import zipfile

VERSION = "1.0.0"
ATTRIBUTION = "Made with sticker-kit by filep48 (https://github.com/filep48/sticker-kit)"

GUIDE = """\
STICKER KIT GUIDE (v{version})

Goal: WhatsApp stickers of characters from user screenshots, made by CUTTING the
original pixels. Never generate or redraw images. If you cannot run Python, say so
and stop.

0. Run `python sticker_kit.py selftest`. If it fails, report the error and stop.
   It prints MODE: model (best, needs rembg + internet once) or classic (no model).
   Optional upgrade: `pip install "rembg[cpu]"`; if that fails, classic is fine.
1. For each image, make a grid to read coordinates:
     python sticker_kit.py grid IMAGE --out grid.png
   Look at grid.png. Never guess coordinates.
2. Cut one sticker per character, plus one group sticker when several characters
   share an image (3 characters = 4 stickers):
     python sticker_kit.py cut IMAGE --box X0 Y0 X1 Y1 --out NAME
   Read coordinates from the yellow labels, not from preview pixel positions.
   Leave a small margin: anything outside the box is lost. If cut prints
   "WARNING: the cut touches the ... edge", enlarge the box on that side and redo.
   In classic mode keep the margin small: the box edge defines the background. Output: NAME.png and NAME.webp (512x512, white outline).
   Characters touching each other: add seeds, 3-6 points per character on head,
   hair, body, legs and held objects, then pick who to keep:
     --seeds '{{"a": [[x,y],[x,y]], "b": [[x,y]]}}' --keep a
   Background leftovers of a known color: --key R,G,B (repeatable) --tol 24
   Inner parts turned white (a logo or drawing whose fill matches the background,
   like a yellow wing on a yellow sign): add --fill-holes
   Best cleanup: another screenshot of the SAME scene and framing (characters
   elsewhere): --plate OTHER_IMAGE --plate-box X0 Y0 X1 Y1 (box of its characters)
3. Review: python sticker_kit.py sheet NAME1.png NAME2.png --out sheet.png
   Look at sheet.png (each sticker on magenta and on dark grey). Check for holes,
   background leftovers, parts of another character, missing parts (hair, beak,
   held objects) and odd edges. Fix only the failing ones and repeat (2-4 rounds).
4. Deliver:
   - Always: every sticker as a PNG (zip them when there are 3 or more). WhatsApp's
     own sticker creator takes them one by one (sticker tray > Create), no app needed.
   - 3 to 30 stickers: also a pack. Ask the user for a pack name, then
       python sticker_kit.py pack NAME1.webp NAME2.webp ... --title "Pack 1" --author "Me" --out Pack_1.wastickers
     It imports the whole pack at once, but only through a sticker maker app.
   - Plus the review sheet. Tell the user which file is for what in one line each.

Known traps:
- Removing a background color also removes details of that color (a beak the
  color of sand). Check what disappeared after every --key.
- A character cut by the screen edge or a UI panel keeps a straight edge; that is
  expected. Lower the box to drop thin strips of panel.
- Black hair touching black hair has no edge: put seeds on each character's hair.
- Photos of real objects (white shirt on white box) have no edge; cut only what
  has a clear outline and tell the user.
Reply in the user's language. When you hand over the stickers, add this line:
{attribution}
"""

_SESS = {}


def _np():
    import numpy as np
    return np


def _ndi():
    from scipy import ndimage as ndi
    return ndi


def _pil():
    from PIL import Image, ImageDraw, ImageFilter
    return Image, ImageDraw, ImageFilter


def load(path):
    np = _np()
    Image, _, _ = _pil()
    return np.array(Image.open(path).convert("RGB")).astype(np.int16)


def has_model():
    """True if rembg is importable. Offline sandboxes usually lack it."""
    try:
        import rembg  # noqa: F401
        return True
    except Exception:
        return False


def grid(F, out, step=50, max_w=1200):
    """Draw a coordinate grid (original pixel units) to choose boxes and seeds."""
    Image, ImageDraw, _ = _pil()
    im = Image.fromarray(F.astype("uint8"))
    s = min(1.0, max_w / im.width)
    im = im.resize((round(im.width * s), round(im.height * s)))
    d = ImageDraw.Draw(im)
    for x in range(0, F.shape[1], step):
        d.line([(x * s, 0), (x * s, im.height)], fill=(0, 255, 0) if x % (step * 2) == 0 else (0, 110, 0))
        d.text((x * s + 2, 2), str(x), fill="yellow")
    for y in range(0, F.shape[0], step):
        d.line([(0, y * s), (im.width, y * s)], fill=(0, 255, 0) if y % (step * 2) == 0 else (0, 110, 0))
        d.text((2, y * s + 2), str(y), fill="yellow")
    im.save(out)


def seg(F, box=None, model="birefnet-general", force_classic=False):
    """Foreground mask (0-255, size of F). Uses rembg when present, else seg_classic."""
    np = _np()
    if force_classic or not has_model():
        return seg_classic(F, box)
    from rembg import new_session, remove
    Image, _, _ = _pil()
    if model not in _SESS:
        _SESS[model] = new_session(model)
    x0, y0, x1, y1 = box or (0, 0, F.shape[1], F.shape[0])
    crop = Image.fromarray(F[y0:y1, x0:x1].astype(np.uint8))
    a = np.array(remove(crop, session=_SESS[model], only_mask=True))
    full = np.zeros(F.shape[:2], np.uint8)
    full[y0:y1, x0:x1] = a
    return full


def seg_classic(F, box=None, border_tol=18, iters=6):
    """No model: box-edge colors connected to the edge are background, refined with
    OpenCV GrabCut when available. Works well on flat game art with a tight box."""
    np, ndi = _np(), _ndi()
    x0, y0, x1, y1 = box or (0, 0, F.shape[1], F.shape[0])
    C = F[y0:y1, x0:x1]
    h, w = C.shape[:2]
    edge = np.concatenate([C[0], C[-1], C[:, 0], C[:, -1]]).reshape(-1, 3)
    q = (edge // 12) * 12
    _, inv, counts = np.unique(q, axis=0, return_inverse=True, return_counts=True)
    inv = inv.ravel()
    keep = np.where(counts >= max(8, 0.02 * len(edge)))[0]
    bgcols = [edge[inv == i].mean(0) for i in keep]
    shares = counts[keep] / len(edge)
    Cf = C.astype(float)
    near = np.zeros((h, w), bool)       # sure background
    near_soft = np.zeros((h, w), bool)  # small scenery objects: may resemble the character
    for c, share in zip(bgcols, shares):
        if share < 0.06:
            near_soft |= np.abs(Cf - c).max(2) < 8
            continue
        near |= np.abs(Cf - c).max(2) < border_tol
        # shadow = the same background, darker and uniform (ignore near-zero channels)
        ch = c > 40
        if ch.sum() >= 2:
            r = (Cf[..., ch] + 1) / (c[ch] + 1)
            near |= ((r.max(2) < 0.97) & (r.min(2) > 0.5) & ((r.max(2) - r.min(2)) < 0.1)
                     & (np.abs(Cf[..., ~ch] - c[~ch]).max(2, initial=0) < 25))

    def border_connected(mask):
        lab, _ = ndi.label(mask)
        bl = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
        return np.isin(lab, list(bl))

    sure_bg = border_connected(near)
    bg = border_connected(near | near_soft)
    fg = ~bg
    try:
        import cv2
        gc = np.where(bg, cv2.GC_PR_BGD, cv2.GC_PR_FGD).astype(np.uint8)
        gc[ndi.binary_erosion(sure_bg, iterations=4)] = cv2.GC_BGD
        gc[ndi.binary_erosion(fg, iterations=6)] = cv2.GC_FGD
        bgm, fgm = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
        cv2.grabCut(np.ascontiguousarray(C[..., ::-1].astype(np.uint8)), gc, None, bgm, fgm,
                    iters, cv2.GC_INIT_WITH_MASK)
        fg = (gc == cv2.GC_FGD) | (gc == cv2.GC_PR_FGD)
    except ImportError:
        pass
    full = np.zeros(F.shape[:2], np.uint8)
    full[y0:y1, x0:x1] = fg * 255
    return full


def aligned(F, G, rows=(150, 550)):
    """Mean abs difference on a character-free band. Below 0.5: pixel-aligned frames."""
    np = _np()
    r0, r1 = rows
    return float(np.abs(F[r0:r1] - G[r0:r1]).mean())


def plate_bg(F, others, other_masks, tol=20):
    """Background by subtraction: a pixel is background if it matches another frame of
    the same scene where no character covers it. Also flags shadows."""
    np, ndi = _np(), _ndi()
    Fb = ndi.uniform_filter(F.astype(float), (3, 3, 1))
    ev = np.zeros(F.shape[:2], bool)
    for G, gm in zip(others, other_masks):
        Gb = ndi.uniform_filter(G.astype(float), (3, 3, 1))
        free = ~ndi.binary_dilation(gm, iterations=3)
        same = np.abs(Fb - Gb).max(2) < tol
        r = (Fb + 1) / (Gb + 1)
        shadow = (r.max(2) < 0.97) & (r.min(2) > 0.55) & ((r.max(2) - r.min(2)) < 0.1)
        ev |= free & (same | shadow)
    return ev


def color_key(F, colors, tol=24, rows=None):
    """Pixels near given background colors; rows=(y0, y1) limits the area."""
    np = _np()
    k = np.zeros(F.shape[:2], bool)
    for c in colors:
        k |= np.abs(F - np.array(c)).max(2) < tol
    if rows:
        lim = np.zeros_like(k)
        lim[rows[0]:rows[1]] = True
        k &= lim
    return k


def split(F, m, seeds, radius=6):
    """Separate touching characters: watershed on the color gradient from seeds.
    seeds = {"name": [(x, y), ...]}. Returns labels 1..N in seeds order (0 = background)."""
    np = _np()
    from skimage.filters import sobel
    from skimage.segmentation import watershed
    mk = np.zeros(m.shape, np.int32)
    yy, xx = np.ogrid[:m.shape[0], :m.shape[1]]
    for i, k in enumerate(seeds):
        for x, y in seeds[k]:
            mk[(yy - y) ** 2 + (xx - x) ** 2 <= radius * radius] = i + 1
    mk[~m] = 0
    elev = np.max([sobel(F[..., c] / 255.0) for c in range(3)], 0)
    return watershed(elev, mk, mask=m)


def clean(m, open_it=2, keep_min=0.03, hole_max=0.01, protect_bg=None, keep_thin_far_from=None):
    """Opening, drop small islands, fill small holes. protect_bg: pixels never refilled
    (removed background colors). keep_thin_far_from: only open near that mask, so thin
    details (tendrils, hair) survive elsewhere."""
    np, ndi = _np(), _ndi()
    op = ndi.binary_opening(m, iterations=open_it) if open_it else m
    if keep_thin_far_from is not None:
        m = np.where(ndi.binary_dilation(keep_thin_far_from, iterations=4), op, m)
    else:
        m = op
    lab, n = ndi.label(m)
    if n:
        sz = ndi.sum(m, lab, range(1, n + 1))
        m = np.isin(lab, [i + 1 for i, s in enumerate(sz) if s >= keep_min * sz.max()])
    holes = ndi.binary_fill_holes(m) & ~m
    if protect_bg is not None:
        holes &= ~ndi.binary_dilation(protect_bg, iterations=3)
    hl, hn = ndi.label(holes)
    if hn:
        hs = ndi.sum(holes, hl, range(1, hn + 1))
        m |= np.isin(hl, [i + 1 for i, s in enumerate(hs) if s < hole_max * m.sum()])
    return m


def cutout(F, m, a=None):
    """RGBA cropped to the mask bbox; soft edge from the model mask when given."""
    np, ndi = _np(), _ndi()
    Image, _, _ = _pil()
    alpha = np.where(m, 255, 0).astype(float)
    if a is not None:
        e = m & ~ndi.binary_erosion(m)
        alpha[e] = np.clip(a[e], 170, 255)
    im = Image.fromarray(np.dstack([F.astype(np.uint8), alpha.astype(np.uint8)]), "RGBA")
    return im.crop(im.getbbox())


def sticker(F, m, a=None, outline=True, size=512, pad=16, ow=6, work_px=450):
    """Final 512x512 sticker with white outline. Rescales to ~work_px first so the
    outline has the same thickness whatever the source resolution."""
    np, ndi = _np(), _ndi()
    Image, _, ImageFilter = _pil()
    img = cutout(F, m, a)
    s = work_px / max(img.size)
    if s < 1:
        img = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
    if outline:
        al = np.array(img)[..., 3] > 100
        P = ow + 4
        big = np.pad(al, P)
        yy, xx = np.ogrid[-ow:ow + 1, -ow:ow + 1]
        d = ndi.binary_dilation(big, structure=xx * xx + yy * yy <= ow * ow)
        # close only small gaps; a large enclosed pocket of background stays see-through
        holes = ndi.binary_fill_holes(d) & ~d
        hl, hn = ndi.label(holes)
        if hn:
            hs = ndi.sum(holes, hl, range(1, hn + 1))
            d |= np.isin(hl, [i + 1 for i, v in enumerate(hs) if v < 0.02 * d.sum()])
        white = Image.new("RGBA", (big.shape[1], big.shape[0]), (255, 255, 255, 255))
        white.putalpha(Image.fromarray((d * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1)))
        white.alpha_composite(img, (P, P))
        img = white
    k = (size - 2 * pad) / max(img.size)
    img = img.resize((max(1, round(img.width * k)), max(1, round(img.height * k))), Image.LANCZOS)
    c = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    c.alpha_composite(img, ((size - img.width) // 2, (size - img.height) // 2))
    return c


def save_webp(img, path, limit=100_000):
    """WebP under 100 KB (WhatsApp limit). Returns (bytes, quality)."""
    for q in (95, 90, 85, 80, 75, 70, 60, 50):
        b = io.BytesIO()
        img.save(b, "WEBP", quality=q, method=6)
        if b.tell() < limit:
            break
    with open(path, "wb") as f:
        f.write(b.getvalue())
    return b.tell(), q


def sheet(paths, out, tile=300, cols=4):
    """Review sheet: each sticker on magenta and on dark grey."""
    Image, ImageDraw, _ = _pil()
    rows = (len(paths) + cols - 1) // cols
    c = Image.new("RGB", (cols * tile * 2, rows * (tile + 18)), "white")
    d = ImageDraw.Draw(c)
    for k, p in enumerate(paths):
        i = Image.open(p).convert("RGBA")
        i.thumbnail((tile, tile))
        x, y = (k % cols) * tile * 2, (k // cols) * (tile + 18)
        for j, col in enumerate([(255, 0, 255, 255), (40, 40, 40, 255)]):
            bg = Image.new("RGBA", (tile, tile), col)
            bg.alpha_composite(i, ((tile - i.width) // 2, (tile - i.height) // 2))
            c.paste(bg.convert("RGB"), (x + j * tile, y))
        d.text((x + 4, y + tile + 3), os.path.basename(p), fill="black")
    c.save(out)


def wastickers(webps, title, out, tray, author="Sticker Kit"):
    """Pack that Sticker Maker apps import whole: renamed zip with title/author/tray + webps."""
    Image, _, _ = _pil()
    if not 3 <= len(webps) <= 30:
        raise ValueError("a WhatsApp pack holds 3 to 30 stickers")
    t = Image.open(tray).convert("RGBA").resize((96, 96), Image.LANCZOS)
    tb = io.BytesIO()
    t.save(tb, "PNG")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("title.txt", title)
        z.writestr("author.txt", author)
        z.writestr("tray.png", tb.getvalue())
        for w in webps:
            z.write(w, os.path.basename(w))


def make(F, box, seeds=None, keep=None, keys=(), tol=24, force_classic=False, outline=True,
         plate=None, plate_box=None, fill_holes=False, outline_width=6):
    """One-call cut used by the CLI: segment, subtract a same-scene frame, remove keyed
    colors, split, clean, outline. plate = another frame of the same scene, plate_box =
    the box around the characters in that frame. fill_holes refills every enclosed hole
    with the original pixels (logos and outlined drawings whose inner parts share the
    background color)."""
    np = _np()
    a = seg(F, box, force_classic=force_classic)
    m = a > 128
    if plate is not None:
        if plate.shape != F.shape:
            raise ValueError("--plate image must have the same size as the image")
        pm = seg(plate, plate_box, force_classic=force_classic) > 128 if plate_box else np.zeros(F.shape[:2], bool)
        m &= ~plate_bg(F, [plate], [pm])
    key = color_key(F, keys, tol=tol) if keys else None
    if key is not None:
        m &= ~key
    hole_max = 1.0 if fill_holes else 0.01
    if seeds:
        L = split(F, m, seeds)
        names = list(seeds)
        picked = [names.index(k) + 1 for k in (keep or names)]
        m = clean(np.isin(L, picked), open_it=1, protect_bg=key, hole_max=hole_max)
    else:
        m = clean(m, protect_bg=key, keep_thin_far_from=key, hole_max=hole_max)
    for side in touched_edges(m, box, F.shape):
        print("WARNING: the cut touches the %s edge of --box; the subject may be clipped. "
              "Enlarge the box on that side." % side, file=sys.stderr)
    return sticker(F, m, a if has_model() and not force_classic else None, outline=outline,
                   ow=outline_width)


def touched_edges(m, box, shape, min_px=6):
    """Box sides where the mask runs into the box (not the image border): a clipped subject."""
    x0, y0, x1, y1 = box
    h, w = shape[:2]
    sides = []
    if x0 > 0 and m[y0:y1, x0].sum() >= min_px:
        sides.append("left")
    if x1 < w and m[y0:y1, x1 - 1].sum() >= min_px:
        sides.append("right")
    if y0 > 0 and m[y0, x0:x1].sum() >= min_px:
        sides.append("top")
    if y1 < h and m[y1 - 1, x0:x1].sum() >= min_px:
        sides.append("bottom")
    return sides


def selftest(verbose=True):
    """Synthetic flat-art scene: checks classic cut, split, outline, webp and pack."""
    import tempfile
    np = _np()
    Image, _, _ = _pil()
    out = []

    def check(name, ok, detail=""):
        out.append((name, bool(ok), detail))

    H, W = 400, 600
    F = np.zeros((H, W, 3), np.int16)
    F[:] = (110, 170, 230)                      # sky
    F[300:] = (200, 180, 120)                   # sand
    yy, xx = np.ogrid[:H, :W]
    shadow = ((yy - 330) / 12.0) ** 2 + ((xx - 200) / 70.0) ** 2 <= 1
    F[shadow] = (150, 135, 90)                  # shadow on sand
    a_body = ((yy - 230) / 90.0) ** 2 + ((xx - 200) / 60.0) ** 2 <= 1
    b_body = ((yy - 230) / 90.0) ** 2 + ((xx - 315) / 60.0) ** 2 <= 1
    F[a_body] = (240, 120, 60)                  # character A, orange
    F[b_body & ~a_body] = (90, 60, 160)         # character B, purple, touching A
    beak = ((yy - 200) / 6.0) ** 2 + ((xx - 200) / 6.0) ** 2 <= 1
    F[beak] = (200, 180, 120)                   # detail with the sand color, enclosed
    truth = a_body | (b_body & ~a_body)

    m = seg_classic(F, (110, 120, 395, 345)) > 128
    inter, union = (m & truth).sum(), (m | truth).sum()
    iou = inter / union
    check("classic cut IoU >= 0.95", iou >= 0.95, "IoU %.3f" % iou)
    check("shadow not kept", (m & shadow & ~truth).sum() < 0.1 * shadow.sum())
    mc = clean(m)
    check("enclosed detail refilled", mc[200, 200])

    L = split(F, mc, {"a": [(180, 230), (200, 170)], "b": [(330, 230), (320, 290)]})
    acc_a = ((L == 1) & a_body).sum() / a_body.sum()
    acc_b = ((L == 2) & b_body & ~a_body).sum() / (b_body & ~a_body).sum()
    check("split separates touching characters", acc_a > 0.95 and acc_b > 0.95,
          "A %.3f, B %.3f" % (acc_a, acc_b))

    st = sticker(F, L == 1)
    arr = np.array(st)
    check("sticker is 512x512 RGBA", st.size == (512, 512) and st.mode == "RGBA")
    check("outline is white", (arr[..., 3] > 250).any() and tuple(arr[256, 30][:3]) in ((255, 255, 255), (0, 0, 0)))
    with tempfile.TemporaryDirectory() as d:
        paths = []
        for i in range(3):
            p = os.path.join(d, "s%d.webp" % i)
            size, q = save_webp(st, p)
            paths.append(p)
        check("webp under 100 KB", size < 100_000, "%d bytes" % size)
        tray = os.path.join(d, "t.png")
        st.save(tray)
        pk = os.path.join(d, "p.wastickers")
        wastickers(paths, "Test 1", pk, tray)
        names = set(zipfile.ZipFile(pk).namelist())
        check("pack has title, author, tray and stickers",
              {"title.txt", "author.txt", "tray.png", "s0.webp"} <= names)
    ok = all(r[1] for r in out)
    if verbose:
        for name, good, detail in out:
            print(("PASS " if good else "FAIL ") + name + (" (" + detail + ")" if detail else ""))
        print("MODE:", "model" if has_model() else "classic")
        print("SELFTEST", "OK" if ok else "FAILED", "v" + VERSION)
        print(ATTRIBUTION)
    return ok


def _rgb(s):
    parts = [int(v) for v in s.split(",")]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("color must be R,G,B")
    return tuple(parts)


def main(argv=None):
    p = argparse.ArgumentParser(prog="sticker_kit", description="Cut characters out of screenshots into WhatsApp stickers.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("guide", help="print instructions")
    sub.add_parser("selftest", help="check this file and its dependencies")
    g = sub.add_parser("grid", help="draw a coordinate grid")
    g.add_argument("image")
    g.add_argument("--out", default="grid.png")
    g.add_argument("--step", type=int, default=50)
    c = sub.add_parser("cut", help="cut one sticker")
    c.add_argument("image")
    c.add_argument("--box", type=int, nargs=4, required=True, metavar=("X0", "Y0", "X1", "Y1"))
    c.add_argument("--out", required=True, help="output name without extension")
    c.add_argument("--seeds", help='JSON: {"a": [[x,y],...], "b": [[x,y],...]}')
    c.add_argument("--keep", nargs="+", help="seed names to keep (default: all)")
    c.add_argument("--key", type=_rgb, action="append", default=[], help="background color R,G,B to remove")
    c.add_argument("--tol", type=int, default=24)
    c.add_argument("--plate", help="another screenshot of the same scene (same framing) used as clean background")
    c.add_argument("--plate-box", type=int, nargs=4, metavar=("X0", "Y0", "X1", "Y1"),
                   help="box around the characters in the --plate image")
    c.add_argument("--classic", action="store_true", help="force no-model mode")
    c.add_argument("--fill-holes", action="store_true",
                   help="refill every enclosed hole with the original pixels (logos, outlined drawings)")
    c.add_argument("--outline-width", type=int, default=6, help="white outline width (default 6)")
    c.add_argument("--no-outline", action="store_true")
    s = sub.add_parser("sheet", help="review sheet on magenta and dark grey")
    s.add_argument("images", nargs="+")
    s.add_argument("--out", default="sheet.png")
    k = sub.add_parser("pack", help="build a .wastickers pack")
    k.add_argument("webps", nargs="+")
    k.add_argument("--title", required=True)
    k.add_argument("--author", default="Sticker Kit")
    k.add_argument("--tray", help="PNG for the pack icon (default: first sticker)")
    k.add_argument("--out", required=True)
    a = p.parse_args(argv)

    if a.cmd == "guide":
        print(GUIDE.format(version=VERSION, attribution=ATTRIBUTION))
    elif a.cmd == "selftest":
        sys.exit(0 if selftest() else 1)
    elif a.cmd == "grid":
        grid(load(a.image), a.out, step=a.step)
        print("wrote", a.out)
    elif a.cmd == "cut":
        seeds = {n: [tuple(pt) for pt in pts] for n, pts in json.loads(a.seeds).items()} if a.seeds else None
        st = make(load(a.image), tuple(a.box), seeds=seeds, keep=a.keep, keys=a.key, tol=a.tol,
                  force_classic=a.classic, outline=not a.no_outline,
                  fill_holes=a.fill_holes, outline_width=a.outline_width,
                  plate=load(a.plate) if a.plate else None,
                  plate_box=tuple(a.plate_box) if a.plate_box else None)
        st.save(a.out + ".png")
        size, q = save_webp(st, a.out + ".webp")
        print("wrote %s.png and %s.webp (%d bytes, q%d)" % (a.out, a.out, size, q))
    elif a.cmd == "sheet":
        sheet(a.images, a.out)
        print("wrote", a.out)
    elif a.cmd == "pack":
        tray = a.tray
        if not tray:
            Image, _, _ = _pil()
            tray = a.out + ".tray.png"
            Image.open(a.webps[0]).save(tray)
        wastickers(a.webps, a.title, a.out, tray, author=a.author)
        print("wrote", a.out)


if __name__ == "__main__":
    main()
