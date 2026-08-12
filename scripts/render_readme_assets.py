"""Gera os ativos visuais reproduzíveis do README do MiraiOS v0.14."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs" / "assets"
FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")

NAVY = "#050914"
PANEL = "#0A1222"
PANEL_ALT = "#0D1728"
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
    size: int = 18,
) -> int:
    """Desenha uma etiqueta e retorna sua largura."""
    label_font = font("DejaVuSans-Bold.ttf", size)
    draw = ImageDraw.Draw(canvas)
    left, top = xy
    bounds = draw.textbbox((0, 0), text, font=label_font)
    width = bounds[2] - bounds[0] + 32
    height = size + 22
    draw.rounded_rectangle(
        (left, top, left + width, top + height),
        radius=height // 2,
        fill=fill,
        outline=outline,
        width=2,
    )
    draw.text((left + 16, top + 9), text, font=label_font, fill=text_color)
    return width


def render_hero() -> None:
    """Compõe a marca sobre a arte-base versionada do projeto."""
    width, height = 1600, 640
    background_path = ASSETS / "miraios-hero-background.jpg"
    if background_path.is_file():
        canvas = Image.open(background_path).convert("RGBA")
        canvas = canvas.resize((width, height), Image.Resampling.LANCZOS)
    else:
        canvas = Image.new("RGBA", (width, height), NAVY)

    shade = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shade_draw = ImageDraw.Draw(shade)
    for x in range(1080):
        opacity = int(235 * (1 - (x / 1080) ** 2))
        shade_draw.line((x, 0, x, height), fill=(5, 9, 20, opacity))
    shade_draw.rectangle((0, 0, width, 82), fill=(5, 9, 20, 46))
    canvas = Image.alpha_composite(canvas, shade)

    glow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse((-300, 120, 620, 900), fill=(0, 139, 255, 38))
    glow = glow.filter(ImageFilter.GaussianBlur(120))
    canvas = Image.alpha_composite(canvas, glow)

    draw = ImageDraw.Draw(canvas)
    mark = fit_mark(72)
    canvas.alpha_composite(mark, (92, 58))
    rounded_label(canvas, (184, 72), "PROJECT HIKARI  •  v0.14", size=17)

    title_font = font("DejaVuSans-Bold.ttf", 104)
    title_x, title_y = 92, 158
    draw.text((title_x, title_y), "Mirai", font=title_font, fill=WHITE)
    mirai_width = draw.textlength("Mirai", font=title_font)
    draw.text((title_x + int(mirai_width), title_y), "OS", font=title_font, fill=CYAN)

    tagline_font = font("DejaVuSans.ttf", 32)
    draw.text((98, 290), "THE FUTURE RUNS LOCAL", font=tagline_font, fill="#C8D7EA")

    body_bold = font("DejaVuSans-Bold.ttf", 25)
    body = font("DejaVuSans.ttf", 23)
    draw.text(
        (98, 358),
        "IA LOCAL, VERIFICÁVEL E RECUPERÁVEL.",
        font=body_bold,
        fill=WHITE,
    )
    draw.text(
        (98, 402),
        "Do modelo ONNX à evidência operacional no dispositivo Edge.",
        font=body,
        fill="#AFC1D9",
    )

    label_x = 98
    for label in ("IMPLANTAR", "EXECUTAR", "OBSERVAR", "RECUPERAR"):
        label_x += rounded_label(
            canvas,
            (label_x, 508),
            label,
            text_color="#C7F4FF",
            fill="#071A2A",
            outline="#15516D",
            size=16,
        ) + 14

    canvas.convert("RGB").save(ASSETS / "miraios-hero.png", optimize=True, quality=94)


def render_operating_loop() -> None:
    """Gera uma visão visual compacta dos quatro verbos da promessa."""
    width, height = 1600, 390
    canvas = Image.new("RGBA", (width, height), NAVY)
    draw = ImageDraw.Draw(canvas)

    glow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse((260, 170, 1340, 540), fill=(0, 128, 255, 42))
    canvas = Image.alpha_composite(canvas, glow.filter(ImageFilter.GaussianBlur(90)))
    draw = ImageDraw.Draw(canvas)

    eyebrow = font("DejaVuSans-Bold.ttf", 17)
    heading = font("DejaVuSans-Bold.ttf", 30)
    body = font("DejaVuSans.ttf", 18)
    number_font = font("DejaVuSans-Bold.ttf", 18)

    draw.text((80, 43), "O CICLO OPERACIONAL MIRAI", font=eyebrow, fill=CYAN)
    draw.text(
        (80, 75),
        "Um caminho explícito do artefato à recuperação",
        font=heading,
        fill=WHITE,
    )

    cards = [
        ("01", "IMPLANTAR", "Empacotar, assinar,\nvalidar e ativar.", CYAN),
        ("02", "EXECUTAR", "Inferência local com\ncontrato preservado.", BLUE),
        ("03", "OBSERVAR", "Métricas, benchmark,\nauditoria e evidências.", GREEN),
        ("04", "RECUPERAR", "Gates, falhas explícitas\ne rollback seguro.", YELLOW),
    ]
    card_width, gap, top = 335, 26, 165
    left = 80
    centers: list[tuple[int, int]] = []

    for index, (number, title, description, accent) in enumerate(cards):
        x = left + index * (card_width + gap)
        draw.rounded_rectangle(
            (x, top, x + card_width, top + 158),
            radius=18,
            fill=PANEL,
            outline=PANEL_BORDER,
            width=2,
        )
        draw.rounded_rectangle(
            (x, top, x + 6, top + 158),
            radius=3,
            fill=accent,
        )
        draw.text((x + 30, top + 25), number, font=number_font, fill=accent)
        draw.text((x + 78, top + 21), title, font=eyebrow, fill=WHITE)
        draw.multiline_text(
            (x + 30, top + 67),
            description,
            font=body,
            fill=MUTED,
            spacing=8,
        )
        centers.append((x + card_width, top + 79))

    for index in range(3):
        x1, y = centers[index]
        x2 = left + (index + 1) * (card_width + gap)
        draw.line((x1 + 5, y, x2 - 5, y), fill="#315071", width=3)
        draw.polygon(((x2 - 13, y - 6), (x2 - 5, y), (x2 - 13, y + 6)), fill=CYAN)

    canvas.convert("RGB").save(
        ASSETS / "miraios-operating-loop.png",
        optimize=True,
        quality=94,
    )


DEMO_LINES: list[tuple[str, str]] = [
    ("$ mirai pack examples/dummy_model.onnx --name dummy \\", CYAN),
    ("  --package-version 1.0.0", MUTED),
    ("[MiraiOS] Pacote criado: dummy-1.0.0.mirai", GREEN),
    ("$ mirai passport create dummy-1.0.0.mirai \\", CYAN),
    ("  --output model-passport.json --intended-use 'demo local'", MUTED),
    ("[MiraiOS] Model Passport criado e ligado ao artefato", GREEN),
    ("$ mirai arena run dummy-1.0.0.mirai \\", CYAN),
    ("  --output benchmark.json --category sbc-arm --runs 100", MUTED),
    ("[MiraiOS] Arena Result salvo • hardware privacy-safe", GREEN),
    ("$ mirai failure-lab dummy-1.0.0.mirai --output failure-lab.json", CYAN),
    ("[MiraiOS] Falhas controladas detectadas em cópias temporárias", GREEN),
    ("$ mirai evidence create report.json --output entrega.mirai-evidence \\", CYAN),
    ("  --artifact dummy-1.0.0.mirai --passport model-passport.json \\", MUTED),
    ("  --include benchmark.json --include failure-lab.json", MUTED),
    ("[MiraiOS] Evidence Bundle íntegro e portátil", GREEN),
]


def terminal_frame(visible_lines: int, *, cursor_visible: bool) -> Image.Image:
    """Desenha um frame da demonstração da cadeia de evidência v0.14."""
    width, height = 1280, 720
    canvas = Image.new("RGB", (width, height), NAVY)
    draw = ImageDraw.Draw(canvas)

    glow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse((820, -300, 1450, 330), fill=(0, 130, 255, 54))
    glow = glow.filter(ImageFilter.GaussianBlur(105))
    canvas = Image.alpha_composite(canvas.convert("RGBA"), glow).convert("RGB")
    draw = ImageDraw.Draw(canvas)

    terminal = (58, 42, 1222, 655)
    draw.rounded_rectangle(terminal, radius=22, fill=PANEL, outline=PANEL_BORDER, width=2)
    draw.rounded_rectangle((59, 43, 1221, 108), radius=21, fill=PANEL_ALT)
    draw.rectangle((59, 84, 1221, 108), fill=PANEL_ALT)
    for index, color in enumerate(("#FF6B6B", YELLOW, GREEN)):
        center_x = 92 + index * 29
        draw.ellipse((center_x - 7, 73 - 7, center_x + 7, 73 + 7), fill=color)

    header_font = font("DejaVuSans-Bold.ttf", 20)
    draw.text((447, 61), "MiraiOS v0.14  •  Evidence workflow", font=header_font, fill="#C7D6EB")

    mono = font("DejaVuSansMono.ttf", 18)
    line_y = 125
    for text, color in DEMO_LINES[:visible_lines]:
        if text.startswith("$ "):
            draw.text((88, line_y), "$", font=mono, fill=YELLOW)
            draw.text((112, line_y), text[2:], font=mono, fill=color)
        else:
            draw.text((88, line_y), text, font=mono, fill=color)
        line_y += 32

    if cursor_visible and visible_lines < len(DEMO_LINES):
        draw.rounded_rectangle((88, line_y + 3, 101, line_y + 25), radius=2, fill=CYAN)

    footer_font = font("DejaVuSans-Bold.ttf", 15)
    footer = "MODEL  →  PASSPORT  →  BENCHMARK  →  FAILURE LAB  →  EVIDENCE"
    footer_width = draw.textlength(footer, font=footer_font)
    draw.text(((width - footer_width) / 2, 682), footer, font=footer_font, fill="#607895")
    return canvas


def render_demo() -> None:
    """Gera um GIF curto mostrando comandos disponíveis na CLI v0.14."""
    frames: list[Image.Image] = [terminal_frame(0, cursor_visible=True)]
    durations: list[int] = [700]

    pauses = {1, 3, 4, 6, 7, 9, 10, 11, 12, 15}
    for visible_lines in range(1, len(DEMO_LINES) + 1):
        frames.append(
            terminal_frame(
                visible_lines,
                cursor_visible=visible_lines < len(DEMO_LINES),
            )
        )
        durations.append(700 if visible_lines in pauses else 390)

    frames.append(terminal_frame(len(DEMO_LINES), cursor_visible=False))
    durations.append(3200)
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
    render_operating_loop()
    render_demo()
    print(f"Ativos gerados em {ASSETS}")


if __name__ == "__main__":
    main()
