#!/usr/bin/env python3
"""
Compose a "snake" (boustrophedon) figure from several folders of step images.

Each input folder is rendered as one labelled section: its images are cropped to
their content and laid out left-to-right on the first row, right-to-left on the
next row, and so on, with arrows drawn between consecutive images. Sections are
stacked vertically, each labelled by the folder name (or a custom label).

This reproduces the style of ``paper/figures/staircase_errors.pdf``.

Usage:
    python tools/snake_figure.py FOLDER [FOLDER ...] --output out.png

    # Custom labels (one per folder, same order). Either pass them as separate
    # arguments, or as one ";"-separated string (works with --labels=... too):
    python tools/snake_figure.py uf/animation clayg/animation \
        --labels "UF" "CAYG(l=0, g=1)" --output out.png
    python tools/snake_figure.py uf/animation clayg/animation \
        --labels="UF;CAYG(l=0, g=1)" --output out.png

    # Different column count / image pattern:
    python tools/snake_figure.py FOLDER --columns 3 --image-glob "frame_*.png"

The images are expected to have a transparent (or white) margin around the
actual content; that margin is cropped away automatically. If your images do not
crop well, render them with a transparent background (e.g. matplotlib
``savefig(..., transparent=True)``) so the content bounding box can be detected.

Arrow types (optional ``arrows.txt`` in each folder)
---------------------------------------------------
Drop a file named ``arrows.txt`` (see ``--arrows-name``) next to the images.
Each non-comment line describes the arrow(s) for one gap between consecutive
panels, in order (gap 0 is between the first two panels, i.e. errors -> step 0
when an errors image is shown). Recognised words:

    grow     black solid    (cluster growth)
    merge    black dotted   (cluster fusion; aliases: fuse, fusion)
    measure  blue  solid    (new measurement round; alias: measurement)
    correct  red   solid    (correction applied; alias: correction)

Put several words on one line to draw that many parallel arrows between the two
panels, e.g. ``measure grow`` draws a blue and a black arrow side by side. A
blank line keeps the default (single black arrow); ``none`` draws nothing. Lines
starting with ``#`` are ignored.
"""

import argparse
import glob
import os
import re
import sys

from PIL import Image, ImageChops, ImageDraw, ImageFont


# Arrow styles selectable per gap from a folder's ``arrows.txt``.
DEFAULT_ARROW_STYLE = {"color": (0, 0, 0, 255), "dotted": False}
ARROW_STYLES = {
    "grow":        {"color": (0, 0, 0, 255),     "dotted": False},
    "growth":      {"color": (0, 0, 0, 255),     "dotted": False},
    "merge":       {"color": (0, 0, 0, 255),     "dotted": True},
    "fuse":        {"color": (0, 0, 0, 255),     "dotted": True},
    "fusion":      {"color": (0, 0, 0, 255),     "dotted": True},
    "measure":     {"color": (0, 90, 200, 255),  "dotted": False},
    "measurement": {"color": (0, 90, 200, 255),  "dotted": False},
    "correct":     {"color": (215, 25, 28, 255), "dotted": False},
    "correction":  {"color": (215, 25, 28, 255), "dotted": False},
}
_ARROW_NONE = {"none", "skip", "-"}


def natural_key(path):
    """Sort key that orders ``step_2`` before ``step_10``."""
    name = os.path.basename(path)
    parts = re.split(r'(\d+)', name)
    return [int(p) if p.isdigit() else p for p in parts]


def find_images(folder, image_glob, errors_name="errors.png"):
    """Return ``(sorted image paths, base directory)`` for a folder.

    Looks in the folder itself and, as a fallback, in an ``animation``
    subdirectory (where the renderer writes its frames). If an errors image is
    present in the same directory, it is placed first so the section starts by
    showing the errors before the decoding steps.
    """
    base = folder
    images = glob.glob(os.path.join(base, image_glob))
    if not images:
        base = os.path.join(folder, "animation")
        images = glob.glob(os.path.join(base, image_glob))
    images = sorted(images, key=natural_key)
    if errors_name:
        errors_path = os.path.join(base, errors_name)
        if os.path.isfile(errors_path):
            images = [errors_path] + images
    return images, base


def parse_arrow_line(line):
    """Turn one ``arrows.txt`` line into a list of arrow style dicts.

    Blank -> the default single black arrow; ``none`` -> no arrow; otherwise one
    style per recognised word, in the given order (so several words draw several
    parallel arrows).
    """
    tokens = line.split()
    if not tokens:
        return [DEFAULT_ARROW_STYLE]
    if len(tokens) == 1 and tokens[0].lower() in _ARROW_NONE:
        return []
    styles = []
    for tok in tokens:
        key = tok.lower()
        if key in _ARROW_NONE:
            continue
        style = ARROW_STYLES.get(key)
        if style is None:
            print(f"[WARN] arrows: unknown type '{tok}' (ignored)")
            continue
        styles.append(style)
    return styles or [DEFAULT_ARROW_STYLE]


def load_arrow_specs(base, name, n_gaps):
    """Return a list of ``n_gaps`` arrow specs (each a list of style dicts).

    Reads ``<base>/<name>`` if present: one entry per non-``#`` line, in order.
    Missing file or missing lines fall back to the default single black arrow.
    """
    specs = [[DEFAULT_ARROW_STYLE] for _ in range(max(0, n_gaps))]
    if not name:
        return specs
    path = os.path.join(base, name)
    if not os.path.isfile(path):
        return specs

    entries = []
    with open(path) as fh:
        for raw in fh:
            if raw.lstrip().startswith('#'):
                continue
            entries.append(parse_arrow_line(raw.split('#', 1)[0].strip()))

    if len(entries) > n_gaps:
        print(f"[WARN] {path}: {len(entries)} arrow lines for {n_gaps} gaps; extra ignored")
    for i in range(min(n_gaps, len(entries))):
        specs[i] = entries[i]
    print(f"[INFO] {path}: {len(entries)} arrow spec line(s)")
    return specs


def content_bbox(img):
    """Bounding box of the non-transparent (or non-white) content of an image."""
    img = img.convert("RGBA")
    bbox = img.getchannel("A").getbbox()
    if bbox is not None:
        return bbox
    # No alpha information: fall back to the non-white region.
    rgb = img.convert("RGB")
    background = Image.new("RGB", rgb.size, (255, 255, 255))
    return ImageChops.difference(rgb, background).getbbox()


def union_bbox(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def load_cropped_images(paths, padding, per_image_crop):
    """Load and crop the images of one section to a common size.

    By default a single bounding box (the union over all frames) is used so that
    the graph stays in the same place across frames. With ``per_image_crop`` each
    image is cropped to its own content and then padded to the common size.
    """
    raw = [Image.open(p).convert("RGBA") for p in paths]

    if per_image_crop:
        cropped = []
        for img in raw:
            bbox = content_bbox(img)
            cropped.append(img.crop(bbox) if bbox else img)
        max_w = max(c.width for c in cropped)
        max_h = max(c.height for c in cropped)
        out = []
        for c in cropped:
            canvas = Image.new("RGBA", (max_w, max_h), (0, 0, 0, 0))
            canvas.alpha_composite(c, ((max_w - c.width) // 2, (max_h - c.height) // 2))
            out.append(canvas)
        cropped = out
    else:
        bbox = None
        for img in raw:
            bbox = union_bbox(bbox, content_bbox(img))
        if bbox is None:
            bbox = (0, 0, raw[0].width, raw[0].height)
        cropped = [img.crop(bbox) for img in raw]

    if padding:
        padded = []
        for c in cropped:
            canvas = Image.new("RGBA", (c.width + 2 * padding, c.height + 2 * padding), (0, 0, 0, 0))
            canvas.alpha_composite(c, (padding, padding))
            padded.append(canvas)
        cropped = padded

    return cropped


def snake_cell(idx, columns):
    """Return the (row, visual_column) of the idx-th image in snake order."""
    row = idx // columns
    pos_in_row = idx % columns
    col = pos_in_row if row % 2 == 0 else columns - 1 - pos_in_row
    return row, col


def load_font(size):
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except Exception:
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()


def draw_arrow(draw, start, end, color, width, head, dotted=False):
    """Draw a straight arrow from ``start`` to ``end``.

    With ``dotted`` the shaft is drawn as short dashes; the head stays solid.
    """
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = (dx * dx + dy * dy) ** 0.5
    if length == 0:
        return
    head = min(head, length)  # never let the head be longer than the arrow
    ux, uy = dx / length, dy / length
    px, py = -uy, ux  # perpendicular unit vector
    base = (end[0] - ux * head, end[1] - uy * head)
    # Stop the shaft at the base of the head so the tip is not blunted by the line.
    if dotted:
        shaft = length - head
        on, off = max(2, int(width * 0.9)), max(4, int(width * 1.9))
        pos = 0.0
        while pos < shaft:
            a = (start[0] + ux * pos, start[1] + uy * pos)
            stop = min(pos + on, shaft)
            b = (start[0] + ux * stop, start[1] + uy * stop)
            draw.line([a, b], fill=color, width=width)
            pos += on + off
    else:
        draw.line([start, base], fill=color, width=width)
    left = (base[0] + px * head * 0.5, base[1] + py * head * 0.5)
    right = (base[0] - px * head * 0.5, base[1] - py * head * 0.5)
    draw.polygon([end, left, right], fill=color)


def draw_parallel_arrows(draw, start, end, styles, width, head, sep):
    """Draw one arrow per style between ``start`` and ``end``, spread sideways.

    The styles are laid out in reading order: left-to-right for a vertical
    (top-down) arrow, top-to-bottom for a horizontal one -- so ``measure grow``
    is a blue arrow then a black arrow, in that visual order.
    """
    if not styles:
        return
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = (dx * dx + dy * dy) ** 0.5
    if length == 0:
        return
    ux, uy = dx / length, dy / length
    px, py = -uy, ux  # perpendicular unit vector
    # Orient the spread so increasing style index moves right (vertical arrow)
    # or down (horizontal arrow), regardless of the flow direction.
    if abs(px) >= abs(py):
        if px < 0:
            px, py = -px, -py
    elif py < 0:
        px, py = -px, -py
    k = len(styles)
    for j, style in enumerate(styles):
        off = (j - (k - 1) / 2.0) * sep
        s = (start[0] + px * off, start[1] + py * off)
        e = (end[0] + px * off, end[1] + py * off)
        draw_arrow(draw, s, e, style["color"], width, head, style["dotted"])


def build_section(images, columns, spacing_x, spacing_y, arrow_specs, arrow_width, head, arrow_sep):
    """Lay out one folder's images into a snake with arrows. Returns an RGBA image.

    ``arrow_specs[i]`` is the list of arrow styles for the gap after image ``i``.
    """
    n = len(images)
    img_w = images[0].width
    img_h = images[0].height
    rows = (n + columns - 1) // columns

    width = columns * img_w + (columns - 1) * spacing_x
    height = rows * img_h + (rows - 1) * spacing_y
    section = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    # Cell offsets in snake order.
    offsets = []
    for idx in range(n):
        row, col = snake_cell(idx, columns)
        x_off = col * (img_w + spacing_x)
        y_off = row * (img_h + spacing_y)
        offsets.append((x_off, y_off))
        section.alpha_composite(images[idx], (x_off, y_off))

    draw = ImageDraw.Draw(section, "RGBA")
    inset_x = min(8, spacing_x // 4)  # keep arrows just inside the gap, not on the image
    inset_y = min(8, spacing_y // 4)
    for idx in range(n - 1):
        (x0, y0), (x1, y1) = offsets[idx], offsets[idx + 1]
        same_row = (idx // columns) == ((idx + 1) // columns)
        if same_row:
            cy = y0 + img_h / 2
            if x1 > x0:  # flowing right
                start = (x0 + img_w + inset_x, cy)
                end = (x1 - inset_x, cy)
            else:        # flowing left
                start = (x0 - inset_x, cy)
                end = (x1 + img_w + inset_x, cy)
        else:
            # Drop down to the next row in the same visual column.
            cx = x0 + img_w / 2
            start = (cx, y0 + img_h + inset_y)
            end = (cx, y1 - inset_y)
        styles = arrow_specs[idx] if idx < len(arrow_specs) else [DEFAULT_ARROW_STYLE]
        draw_parallel_arrows(draw, start, end, styles, arrow_width, head, arrow_sep)

    return section


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('folders', nargs='+', help='Folders of step images, one section each.')
    parser.add_argument('--output', '-o', default='snake_figure.png', help='Output image path.')
    parser.add_argument('--labels', nargs='+', default=None,
                        help='Section labels (one per folder). Either several arguments, or a '
                             'single ";"-separated string (so --labels=... works). '
                             'Defaults to folder names.')
    parser.add_argument('--columns', type=int, default=4, help='Images per row (default: 4).')
    parser.add_argument('--image-glob', default='step_*.png', help='Glob for images in each folder.')
    parser.add_argument('--errors-name', default='errors.png',
                        help='Errors image shown first in each section (set empty to disable).')
    parser.add_argument('--spacing-x', type=int, default=80, help='Horizontal gap between images.')
    parser.add_argument('--spacing-y', type=int, default=70, help='Vertical gap between rows.')
    parser.add_argument('--section-gap', type=int, default=60, help='Gap between sections.')
    parser.add_argument('--padding', type=int, default=0, help='Extra padding around each image after cropping.')
    parser.add_argument('--label-size', type=int, default=100, help='Font size for section labels.')
    parser.add_argument('--arrow-width', type=int, default=10, help='Arrow line width.')
    parser.add_argument('--arrow-head', type=int, default=38, help='Arrow head size.')
    parser.add_argument('--arrow-sep', type=int, default=None,
                        help='Sideways spacing between parallel arrows of one gap '
                             '(default: 2.6x --arrow-width).')
    parser.add_argument('--arrows-name', default='arrows.txt',
                        help='Per-folder arrow-type file (set empty to disable). See module docstring.')
    parser.add_argument('--per-image-crop', action='store_true',
                        help='Crop each image to its own content (default: common box per section).')
    parser.add_argument('--transparent', action='store_true', help='Transparent background (default: white).')
    parser.add_argument('--dpi', type=int, default=300, help='DPI metadata for the saved PNG.')
    args = parser.parse_args()

    # Allow one ";"-separated string as well as several arguments, so that
    # --labels="A;B" (which argparse's "=" form limits to a single token) works.
    if args.labels and len(args.labels) == 1 and ';' in args.labels[0]:
        args.labels = [s.strip() for s in args.labels[0].split(';')]

    if args.labels and len(args.labels) != len(args.folders):
        parser.error(f"--labels expects {len(args.folders)} labels, got {len(args.labels)}.")

    arrow_sep = args.arrow_sep if args.arrow_sep is not None else int(round(args.arrow_width * 5.6))
    font = load_font(args.label_size)
    label_band = args.label_size + 12  # vertical space reserved for each label

    # Build every section first to know the final canvas size.
    sections = []
    labels = []
    for i, folder in enumerate(args.folders):
        paths, base = find_images(folder, args.image_glob, args.errors_name)
        if not paths:
            print(f"[WARN] No images matching '{args.image_glob}' in {folder}; skipping.")
            continue
        images = load_cropped_images(paths, args.padding, args.per_image_crop)
        arrow_specs = load_arrow_specs(base, args.arrows_name, len(paths) - 1)
        section = build_section(images, args.columns, args.spacing_x, args.spacing_y,
                                arrow_specs, args.arrow_width, args.arrow_head, arrow_sep)
        sections.append(section)
        labels.append(args.labels[i] if args.labels else os.path.basename(os.path.normpath(folder)))
        print(f"[INFO] {folder}: {len(paths)} images -> {section.width}x{section.height}")

    if not sections:
        print("[ERROR] No sections to render.")
        sys.exit(1)

    canvas_w = max(s.width for s in sections)
    canvas_h = sum(s.height + label_band for s in sections) + args.section_gap * (len(sections) - 1)

    bg = (0, 0, 0, 0) if args.transparent else (255, 255, 255, 255)
    canvas = Image.new("RGBA", (canvas_w, canvas_h), bg)
    draw = ImageDraw.Draw(canvas)

    y = 0
    for section, label in zip(sections, labels):
        draw.text((0, y), label, fill=(0, 0, 0, 255), font=font)
        y += label_band
        canvas.alpha_composite(section, (0, y))
        y += section.height + args.section_gap

    if not args.transparent:
        canvas = canvas.convert("RGB")
    canvas.save(args.output, dpi=(args.dpi, args.dpi))
    print(f"[INFO] Saved {args.output} ({canvas_w}x{canvas_h})")


if __name__ == '__main__':
    main()
