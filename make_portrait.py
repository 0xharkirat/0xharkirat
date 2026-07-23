"""Dither the portrait into animated frames for the profile card.

Writes portrait-frames.json, which build_card.py embeds. Kept separate because this
needs Pillow and only has to run when the photo changes, so the daily workflow that
rebuilds the card stays stdlib-only.

Each frame re-dithers the same image with the threshold displaced by a field of slow
travelling waves, so cells sitting near the threshold flip while deep blacks and whites
never move. The grain flows, the portrait does not.

    python3 make_portrait.py              # use the GitHub avatar
    python3 make_portrait.py photo.jpg    # use a local photo
"""

import base64
import io
import json
import math
import os
import sys
import urllib.request

from PIL import Image, ImageOps

FRAMES = 16
GRID_W = 120          # dither resolution; the card scales this up with hard pixel edges
DURATION = 2.8        # seconds per loop
BOX_W = 330           # display width in card units
BOX_H = 520           # display height the art column allows
OUT = "portrait-frames.json"

# Travelling waves that displace the dither threshold, in 0-255 levels.
#
# Independent random jitter per cell reads as television static, and slowing that down
# only turns it into a strobe. Water needs the disturbance to be coherent: neighbouring
# cells have to move together so the eye can follow a ripple across the surface.
#
# Each entry is (wavelength x, wavelength y, cycles per loop, amplitude, phase). Cycles
# per loop must be a whole number or the loop will jump when it wraps - selfcheck()
# enforces that. Wavelengths are deliberately not multiples of each other, otherwise the
# crests line up and it reads as corduroy rather than water.
# Two components travel one way and one comes back against them; that interference is
# what stops it looking like a light sweeping across and starts it looking like a
# surface. Wavelengths are short enough to fit a dozen or so ripples across the frame -
# stretch them much longer and it reads as slow lighting instead of water.
WAVES = [
    (12, 17, 1, 8, 0.0),
    (-14, 9, 1, 7, 1.7),
    (8, -21, 2, 5, 3.1),
    (19, 13, -1, 5, 5.0),
]

# Which pixels become ink, and in what colour. Everything else is transparent, so the
# card background shows through and the portrait has no rectangle around it.
#
# The two themes need opposite polarity to both look photographic. The light card is ink
# on paper, so shadows are the ink. The dark card is light on a screen, so the lit parts
# of the face glow and shadows fall away into the background - map it the other way and
# the face renders as a solid pale blob.
THEMES = {
    "dark": {"ink": 1, "colour": "#c9d1d9"},    # bright pixels glow
    "light": {"ink": 0, "colour": "#24292f"},   # dark pixels are the ink
}


def source(path=None):
    if path:
        return Image.open(path)
    with urllib.request.urlopen("https://api.github.com/users/0xharkirat", timeout=30) as r:
        url = json.load(r)["avatar_url"]
    with urllib.request.urlopen(f"{url}&s=640", timeout=30) as r:
        return Image.open(io.BytesIO(r.read()))


def ripple(w, h, t):
    """Threshold displacement field at loop position `t` (0 to 1)."""
    field = []
    for y in range(h):
        row = []
        for x in range(w):
            v = 0.0
            for lx, ly, cycles, amp, phase in WAVES:
                v += amp * math.sin(2 * math.pi * (x / lx + y / ly - cycles * t) + phase)
            row.append(v)
        field.append(row)
    return field


def dither(buf, w, h):
    """Floyd-Steinberg, serpentine, binary output - the Dithering Studio settings."""
    buf = [row[:] for row in buf]
    out = bytearray(w * h)
    for y in range(h):
        xs = range(w) if y % 2 == 0 else range(w - 1, -1, -1)
        step = 1 if y % 2 == 0 else -1
        for x in xs:
            old = buf[y][x]
            new = 255 if old > 128 else 0
            out[y * w + x] = 1 if new else 0
            err = old - new
            for dx, dy, wt in ((step, 0, 7), (-step, 1, 3), (0, 1, 5), (step, 1, 1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h:
                    buf[ny][nx] += err * wt / 16
    return out


def encode(bits, w, h, ink, colour):
    """1-bit indexed PNG: `ink` is the bit value that draws, the other is transparent.

    Saved straight from mode P - converting with an adaptive palette here would requantise
    and reorder the entries, silently breaking which index means what.
    """
    img = Image.frombytes("P", (w, h), bytes(bits))
    rgb = [int(colour[i:i + 2], 16) for i in (1, 3, 5)]
    palette = [0, 0, 0, 0, 0, 0]
    palette[ink * 3:ink * 3 + 3] = rgb
    img.putpalette(palette)
    blob = io.BytesIO()
    img.save(blob, format="PNG", optimize=True, bits=1, transparency=1 - ink)
    return base64.b64encode(blob.getvalue()).decode()


def selfcheck():
    # a non-integer cycle count makes the last frame jump back to the first
    for lx, ly, cycles, amp, phase in WAVES:
        assert cycles == int(cycles), f"wave {lx}x{ly} has a fractional cycle count"
    start, wrap = ripple(6, 6, 0.0), ripple(6, 6, 1.0)
    worst = max(abs(a - b) for ra, rb in zip(start, wrap) for a, b in zip(ra, rb))
    assert worst < 1e-9, f"loop does not close: {worst:.4f} levels of jump at the wrap"


def main():
    selfcheck()
    img = ImageOps.exif_transpose(source(sys.argv[1] if len(sys.argv) > 1 else None))
    img = img.convert("L")

    # fit the display box to the photo rather than cropping the photo to a fixed box,
    # so a head-and-shoulders avatar and a full-length shot both sit properly
    box_h = min(BOX_H, round(BOX_W * img.height / img.width))
    box_w = round(box_h * img.width / img.height)
    if box_w > BOX_W:
        box_w, box_h = BOX_W, round(BOX_W * img.height / img.width)

    grid_h = max(1, round(GRID_W * box_h / box_w))
    small = img.resize((GRID_W, grid_h), Image.LANCZOS)
    px = [[small.getpixel((x, y)) for x in range(GRID_W)] for y in range(grid_h)]

    frames = {name: [] for name in THEMES}
    for i in range(FRAMES):
        wave = ripple(GRID_W, grid_h, i / FRAMES)
        shaken = [[px[y][x] + wave[y][x] for x in range(GRID_W)] for y in range(grid_h)]
        bits = dither(shaken, GRID_W, grid_h)
        for name, spec in THEMES.items():
            frames[name].append(encode(bits, GRID_W, grid_h, spec["ink"], spec["colour"]))

    payload = {
        "width": box_w, "height": box_h,
        "grid": [GRID_W, grid_h],
        "duration": DURATION,
        "frames": frames,
    }
    with open(OUT, "w") as f:
        json.dump(payload, f, separators=(",", ":"))

    size = os.path.getsize(OUT)
    print(f"wrote {OUT}: {FRAMES} frames, {GRID_W}x{grid_h} dithered, "
          f"shown at {box_w}x{box_h}, {size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
