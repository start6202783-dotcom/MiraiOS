"""Gera o hero e a demonstração animada usados no README do MiraiOS."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs" / "assets"
FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")

NAVY = "#050914"
PANEL = "#0A1222"
PANEL_BORDER = "#1C2D48"
WHITE = "#F4F8FF"
MUTED = "#91A6C4"
CYAN = "#00D9FF"
BLUE = "#247CFF"
GREEN = "#4DE2A8"
YELLOW = "#F7C873"


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    """Carrega uma fonte DejaVu disponível nas imagens oficiais do Python."""
    return ImageFont.truetype(str(FONT_DIR / name), size=size)


def fit_mark(size: int) -> Image.Image:
    """Recorta a transparência externa e redimensiona o símbolo oficial."""
    mark = Image.open(ASSETS / "miraios-mark.png").convert("RGBA")
    bounding_box = mark.getbbox()
    if bounding_box is not None:
        mark = mark.crop(bounding_box)
    mark.thumbnail((size, size), Image.Resampling.LANCZOS)
    return mark


def rounded_label(
    canvas: Image.Image,
    xy: tuple[int, int],
    text: str,
    *,
    text_color: str = CYAN,
    fill: str = "#0B2033",
    outline: str = "#164A66",
) -> None:
    """Desenha uma pequena etiqueta da identidade visual."""
    label_font = font("DejaVuSans-Bold.ttf", 20)
    draw = ImageDraw.Draw(canvas)
    left, top = xy
    bounds = draw.textbbox((0, 0), text, font=label_font)
    width = bounds[2] - bounds[0] + 34
    draw.rounded_rectangle(
        (left, top, left + width, top + 42),
        radius=21,
        fill=fill,
        outline=outline,
        width=2,
    )
    draw.text(
        (left + 17, top + 9),
        text,
        font=label_font,
        fill=text_color,
    )


def render_hero() -> None:
    """Gera um banner limpo com o posicionamento do projeto."""
    width, height = 1600, 640
    canvas = Image.new("RGBA", (width, height), NAVY)
    draw = ImageDraw.Draw(canvas)

    for y in range(height):
        ratio = y / height
        color = (
            int(5 + 2 * ratio),
            int(9 + 8 * ratio),
            int(20 + 17 * ratio),
            255,
        )
        draw.line((0, y, width, y), fill=color)

    grid = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    grid_draw = ImageDraw.Draw(grid)
    for x in range(0, width, 80):
        grid_draw.line((x, 0, x, height), fill=(31, 82, 126, 24), width=1)
    for y in range(0, height, 80):
        grid_draw.line((0, y, width, y), fill=(31, 82, 126, 20), width=1)
    canvas = Image.alpha_composite(canvas, grid)

    glow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse(
        (-120, 40, 650, 810),
        fill=(0, 178, 255, 76),
    )
    glow_draw.ellipse(
        (1180, -300, 1820, 360),
        fill=(32, 89, 255, 45),
    )
    glow = glow.filter(ImageFilter.GaussianBlur(110))
    canvas = Image.alpha_composite(canvas, glow)

    mark = fit_mark(430)
    mark_x = 90 + (430 - mark.width) // 2
    mark_y = 96 + (430 - mark.height) // 2
    canvas.alpha_composite(mark, (mark_x, mark_y))

    draw = ImageDraw.Draw(canvas)
    divider_x = 556
    draw.rounded_rectangle(
        (divider_x, 96, divider_x + 3, 544),
        radius=2,
        fill="#173557",
    )

    rounded_label(canvas, (626, 112), "PROJECT HIKARI  •  v0.8")

    title_font = font("DejaVuSans-Bold.ttf", 104)
    title_y = 188
    draw.text((622, title_y), "Mirai", font=title_font, fill=WHITE)
    mirai_width = draw.textlength("Mirai", font=title_font)
    draw.text(
        (622 + int(mirai_width), title_y),
        "OS",
        font=title_font,
        fill=CYAN,
    )

    tagline_font = font("DejaVuSans.ttf", 38)
    draw.text(
        (628, 326),
        "THE FUTURE RUNS LOCAL",
        font=tagline_font,
        fill="#B9C9DF",
    )
    body_font = font("DejaVuSans.ttf", 26)
    draw.text(
        (630, 392),
        "Deploy, execução e observabilidade para Edge AI.",
        font=body_font,
        fill=MUTED,
    )
    draw.text(
        (630, 431),
        "Do arquivo ONNX ao dispositivo em um único fluxo.",
        font=body_font,
        fill=MUTED,
    )

    label_x = 630
    for label in ("PAIR", "SECURE", "DEPLOY", "RUN"):
        rounded_label(
            canvas,
            (label_x, 502),
            label,
            text_color="#BCEFFF",
            fill="#091C2D",
            outline="#17405B",
        )
        label_width = draw.textlength(
            label,
            font=font("DejaVuSans-Bold.ttf", 20),
        )
        label_x += int(label_width) + 62

    canvas.convert("RGB").save(
        ASSETS / "miraios-hero.png",
        optimize=True,
    )


def terminal_frame(
    visible_lines: int,
    *,
    cursor_visible: bool,
) -> Image.Image:
    """Desenha um frame da demonstração de terminal."""
    width, height = 1200, 675
    canvas = Image.new("RGB", (width, height), NAVY)
    draw = ImageDraw.Draw(canvas)

    glow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse(
        (740, -260, 1320, 320),
        fill=(0, 130, 255, 55),
    )
    glow = glow.filter(ImageFilter.GaussianBlur(100))
    canvas = Image.alpha_composite(canvas.convert("RGBA"), glow).convert("RGB")
    draw = ImageDraw.Draw(canvas)

    terminal = (64, 54, 1136, 606)
    draw.rounded_rectangle(
        terminal,
        radius=22,
        fill=PANEL,
        outline=PANEL_BORDER,
        width=2,
    )
    draw.rounded_rectangle(
        (65, 55, 1135, 116),
        radius=21,
        fill="#0E192B",
    )
    draw.rectangle((65, 92, 1135, 116), fill="#0E192B")
    for index, color in enumerate(("#FF6B6B", "#F7C873", "#4DE2A8")):
        center_x = 96 + index * 30
        draw.ellipse(
            (center_x - 7, 78 - 7, center_x + 7, 78 + 7),
            fill=color,
        )

    header_font = font("DejaVuSans-Bold.ttf", 20)
    draw.text(
        (455, 67),
        "MiraiOS v0.8  •  Hikari Link",
        font=header_font,
        fill="#C7D6EB",
    )

    lines: list[tuple[str, str]] = [
        (
            "$ mirai agent start --host 0.0.0.0",
            CYAN,
        ),
        (
            "[MiraiOS] HTTPS • código e fingerprint exibidos",
            MUTED,
        ),
        (
            "$ mirai device pair edge --url https://192.168.1.40:8080 \\",
            CYAN,
        ),
        (
            "  --code <CÓDIGO> --fingerprint <SHA-256>",
            MUTED,
        ),
        (
            "[MiraiOS] Dispositivo pareado: edge",
            GREEN,
        ),
        (
            "$ mirai doctor --device edge",
            CYAN,
        ),
        (
            "✓ Canal: HTTPS com fingerprint fixado",
            GREEN,
        ),
        (
            "✓ Autenticação: token pareado",
            GREEN,
        ),
        (
            "$ mirai deploy examples/dummy_model.onnx --device edge",
            CYAN,
        ),
        (
            "[MiraiOS] Deployment pronto: 153f2947c78a0313",
            GREEN,
        ),
        (
            "$ mirai run --device edge --input 5.0",
            CYAN,
        ),
        (
            "[MiraiOS] Resultado: 6.0 • acesso revogável",
            GREEN,
        ),
    ]

    mono = font("DejaVuSansMono.ttf", 20)
    line_y = 139
    for index, (text, color) in enumerate(lines[:visible_lines]):
        prefix_x = 96
        if text.startswith("$ "):
            draw.text((prefix_x, line_y), "$", font=mono, fill=YELLOW)
            draw.text((prefix_x + 25, line_y), text[2:], font=mono, fill=color)
        else:
            draw.text((prefix_x, line_y), text, font=mono, fill=color)
        line_y += 36

    if cursor_visible and visible_lines < len(lines):
        draw.rounded_rectangle(
            (96, line_y + 3, 109, line_y + 27),
            radius=2,
            fill=CYAN,
        )

    footer_font = font("DejaVuSans-Bold.ttf", 16)
    footer = "LOCAL-FIRST   •   ONNX   •   OPEN SOURCE"
    footer_width = draw.textlength(footer, font=footer_font)
    draw.text(
        ((width - footer_width) / 2, 632),
        footer,
        font=footer_font,
        fill="#607895",
    )
    return canvas


def render_demo() -> None:
    """Gera um GIF curto mostrando o ciclo real da CLI."""
    frames: list[Image.Image] = []
    durations: list[int] = []

    frames.append(terminal_frame(0, cursor_visible=True))
    durations.append(700)
    for visible_lines in range(1, 13):
        frames.append(
            terminal_frame(
                visible_lines,
                cursor_visible=visible_lines < 12,
            )
        )
        durations.append(760 if visible_lines in {1, 3, 5, 7, 9, 11} else 520)

    frames.append(terminal_frame(12, cursor_visible=False))
    durations.append(3000)
    frames[0].save(
        ASSETS / "miraios-demo.gif",
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=2,
    )


def main() -> None:
    """Regenera os ativos determinísticos do README."""
    ASSETS.mkdir(parents=True, exist_ok=True)
    render_hero()
    render_demo()
    print(f"Ativos gerados em {ASSETS}")


if __name__ == "__main__":
    main()
