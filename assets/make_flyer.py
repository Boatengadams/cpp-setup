"""Render the cpp-setup flyer as a print-ready PNG (A4 at 300 DPI).

The layout is a top-down flow with an explicit cursor so that sections can
never silently run off the page: `assert_fits` fails the build instead.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 2480, 3508          # A4 portrait at 300 dpi
MARGIN = 150
OUT = Path(__file__).with_name("cpp-setup-flyer.png")

INK = (15, 23, 42)
SLATE = (30, 41, 59)
BLUE = (0, 92, 197)
CYAN = (14, 165, 233)
LIGHT = (241, 245, 249)
WHITE = (255, 255, 255)
GREY = (100, 116, 139)
BORDER = (203, 213, 225)
MUTED = (148, 163, 184)

FONT_DIRS = ["/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/truetype/liberation"]
BOLD = "DejaVuSans-Bold.ttf"
REG = "DejaVuSans.ttf"
MONO = "DejaVuSansMono-Bold.ttf"

BAND_H = 1060
FOOTER_H = 250


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    for directory in FONT_DIRS:
        candidate = Path(directory) / name
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def pills(d, x, y, names, filled, max_right, gap=22, row_h=88):
    """Draw wrapped pills; returns the y just below the last row."""
    ex, ey = x, y
    for name in names:
        w = d.textlength(name, font=font(BOLD, 40)) + 72
        if ex > x and ex + w > max_right:      # wrap before overflowing
            ex, ey = x, ey + row_h
        box = [ex, ey, ex + w, ey + row_h - 22]
        if filled:
            d.rounded_rectangle(box, radius=(row_h - 22) // 2, fill=BLUE)
            d.text((ex + 36, ey + 15), name, font=font(BOLD, 40), fill=WHITE)
        else:
            d.rounded_rectangle(box, radius=(row_h - 22) // 2, fill=WHITE, outline=CYAN, width=4)
            d.text((ex + 36, ey + 15), name, font=font(BOLD, 40), fill=BLUE)
        ex += w + gap
    return ey + row_h - 22


def main() -> None:
    img = Image.new("RGB", (W, H), LIGHT)
    d = ImageDraw.Draw(img)

    # ---------------- header band ----------------
    d.rectangle([0, 0, W, BAND_H], fill=INK)
    for i in range(26):
        x = W - 900 + i * 34
        d.polygon([(x, BAND_H), (x + 16, BAND_H), (x + 120, BAND_H - 250), (x + 104, BAND_H - 250)],
                  fill=BLUE if i % 2 == 0 else CYAN)

    d.rounded_rectangle([MARGIN, 130, W - MARGIN, 268], radius=18, fill=SLATE, outline=CYAN, width=4)
    d.text((MARGIN + 46, 168), "$ cpp", font=font(MONO, 60), fill=CYAN)
    d.text((MARGIN + 356, 170), "# done. start writing C++", font=font(MONO, 48), fill=MUTED)

    d.text((MARGIN, 350), "CPP", font=font(BOLD, 268), fill=WHITE)
    title_w = d.textlength("CPP", font=font(BOLD, 268))
    d.text((MARGIN + title_w + 46, 350), "SETUP", font=font(BOLD, 268), fill=CYAN)

    d.text((MARGIN + 6, 680), "One command. Full C++ environment.", font=font(BOLD, 88), fill=WHITE)
    d.text((MARGIN + 6, 800), "Windows  •  Linux  •  macOS", font=font(REG, 58), fill=MUTED)
    d.text((MARGIN + 6, 890), "No admin rights. No manual PATH editing.", font=font(REG, 48), fill=GREY)

    footer_top = H - FOOTER_H
    y = BAND_H + 70
    limit = footer_top - 40

    def need(h, label):
        if y + h > limit:
            raise SystemExit("LAYOUT OVERFLOW: '%s' needs %dpx but only %dpx left" % (label, h, limit - y))

    # ---------------- quick start ----------------
    card_h = 360
    need(card_h, "quick start")
    d.rounded_rectangle([MARGIN, y, W - MARGIN, y + card_h], radius=28, fill=WHITE, outline=BORDER, width=3)
    d.text((MARGIN + 70, y + 44), "GET STARTED IN 3 STEPS", font=font(BOLD, 54), fill=BLUE)
    steps = [
        "git clone https://github.com/boatengadams/cpp-setup.git",
        "cd cpp-setup  &&  ./install.sh   (Windows: install.bat)",
        "cpp",
    ]
    ty = y + 138
    for i, text in enumerate(steps, 1):
        d.ellipse([MARGIN + 70, ty, MARGIN + 70 + 62, ty + 62], fill=BLUE)
        bbox = d.textbbox((0, 0), str(i), font=font(BOLD, 42))
        d.text((MARGIN + 70 + 31 - (bbox[2] - bbox[0]) / 2, ty + 31 - (bbox[3] - bbox[1]) / 2 - 6),
               str(i), font=font(BOLD, 42), fill=WHITE)
        d.text((MARGIN + 168, ty + 8), text, font=font(MONO, 42), fill=INK)
        ty += 82
    y += card_h + 70

    # ---------------- what it does ----------------
    need(96 + 3 * 208, "what it does")
    d.text((MARGIN, y), "WHAT IT DOES FOR YOU", font=font(BOLD, 58), fill=INK)
    y += 96
    features = [
        ("Downloads MinGW-w64", "Latest toolchain, extracted and ready"),
        ("Sets PATH for good", "Persisted in HKCU / your shell rc files"),
        ("Verifies globally", "Proven in a brand new shell"),
        ("Configures VS Code", "4 C++ packs + IntelliSense + debugger"),
        ("Proves it works", "Compiles and runs a real C++ program"),
        ("Resumable", "Fail anytime, run cpp again, it continues"),
    ]
    card_w = (W - 2 * MARGIN - 40) // 2
    for i, (title, sub) in enumerate(features):
        col, row = i % 2, i // 2
        x = MARGIN + col * (card_w + 40)
        cy = y + row * 208
        d.rounded_rectangle([x, cy, x + card_w, cy + 184], radius=22, fill=WHITE, outline=BORDER, width=3)
        d.rounded_rectangle([x + 34, cy + 36, x + 34 + 62, cy + 36 + 62], radius=15, fill=CYAN)
        d.text((x + 34 + 31 - 16, cy + 36 + 31 - 20), "✓", font=font(BOLD, 42), fill=WHITE)
        d.text((x + 132, cy + 38), title, font=font(BOLD, 46), fill=INK)
        d.text((x + 132, cy + 100), sub, font=font(REG, 34), fill=GREY)
    y += 3 * 208 + 36

    # ---------------- extensions ----------------
    need(100 + 2 * 90 + 2 * 100, "extensions")
    d.text((MARGIN, y), "VS CODE EXTENSIONS", font=font(BOLD, 58), fill=INK)
    y += 92
    d.text((MARGIN, y), "Always installed:", font=font(REG, 40), fill=GREY)
    y += 56
    y = pills(d, MARGIN, y, ["C/C++ Extension Pack", "C/C++ (IntelliSense)", "CMake Tools", "Makefile Tools"],
              True, W - MARGIN) + 46
    d.text((MARGIN, y), "First-time setup also adds:", font=font(REG, 40), fill=GREY)
    y += 56
    y = pills(d, MARGIN, y, ["Prettier", "Error Lens", "Header Foundry", "CodeLLDB"],
              False, W - MARGIN) + 56

    # ---------------- result box ----------------
    res_h = 400
    need(res_h, "result box")
    d.rounded_rectangle([MARGIN, y, W - MARGIN, y + res_h], radius=28, fill=INK)
    d.text((MARGIN + 80, y + 44), "AND THEN IT PROVES IT:", font=font(BOLD, 44), fill=CYAN)
    d.text((MARGIN + 80, y + 122), "Congratulations <your name>!", font=font(BOLD, 70), fill=WHITE)
    d.text((MARGIN + 80, y + 216), "You have set up your C++ environment.", font=font(REG, 46), fill=BORDER)
    d.text((MARGIN + 80, y + 282), "Continue with your first program.", font=font(REG, 46), fill=BORDER)

    # ---------------- footer ----------------
    d.line([(MARGIN, footer_top), (W - MARGIN, footer_top)], fill=BORDER, width=4)
    d.text((MARGIN, footer_top + 46), "github.com/boatengadams/cpp-setup", font=font(BOLD, 50), fill=BLUE)
    d.text((MARGIN, footer_top + 122), "MIT License  •  BAGSGRAPHICS", font=font(REG, 38), fill=GREY)

    img.save(OUT, "PNG", dpi=(300, 300))
    print("wrote %s (%dx%d, %d KB)" % (OUT.name, W, H, OUT.stat().st_size // 1024))
    print("content ended at y=%d, footer starts at y=%d" % (y + res_h, footer_top))


if __name__ == "__main__":
    main()
    
    
