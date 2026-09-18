"""Twenty-cover card-news design exploration.

This module intentionally does *not* create a five-card story.  It keeps one
piece of copy fixed, renders twenty independent cover directions, and then
assembles them into five four-up comparison sheets.  Only a user-selected
direction should be expanded into the production five-card set afterwards.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .card_design import SUPPORTED_DESIGN_LANGUAGES


WIDTH, HEIGHT = 1080, 1350
DEFAULT_TITLE = 'Codex에서 ChatGPT 열어서 토큰 아끼는 법'
DEFAULT_SUBTITLE = 'Quick Chat 활용'


def _slug(value: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', value.lower()).strip('-')


def _font_candidates(kind: str, override: str | None = None) -> list[str]:
    root = Path(__file__).resolve().parents[1]
    bundled = root / 'assets/fonts/Cafe24Ssurround-v2.0/Cafe24Ssurround-v2.0.ttf'
    common = [
        str(bundled),
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc',
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
        '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc',
        'C:/Windows/Fonts/malgunbd.ttf',
        'C:/Windows/Fonts/malgun.ttf',
    ]
    if kind == 'serif':
        specific = [
            '/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc',
            'C:/Windows/Fonts/batang.ttc',
            'C:/Windows/Fonts/gungsuh.ttc',
        ]
    elif kind == 'mono':
        specific = [
            '/usr/share/fonts/opentype/noto/NotoSansMonoCJK-Regular.ttc',
            'C:/Windows/Fonts/D2Coding-Ver1.3.2-20180524.ttf',
            str(bundled),
            '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
            'C:/Windows/Fonts/malgun.ttf',
            'C:/Windows/Fonts/consola.ttf',
        ]
    else:
        specific = []
    return ([override] if override else []) + specific + common


def _font(kind: str, size: int, override: str | None = None) -> ImageFont.FreeTypeFont:
    for candidate in _font_candidates(kind, override):
        if candidate and Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    raise RuntimeError('A Korean TrueType/OpenType font is required for card exploration')


def _fit_font(
    draw: ImageDraw.ImageDraw, text: str, kind: str, size: int,
    max_width: int, override: str | None = None, minimum: int = 22,
) -> ImageFont.FreeTypeFont:
    while size >= minimum:
        font = _font(kind, size, override)
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return font
        size -= 2
    return _font(kind, minimum, override)


def _title_lines(title: str) -> list[str]:
    normalized = ' '.join(str(title).split())
    if normalized == DEFAULT_TITLE:
        return ['Codex에서', 'ChatGPT 열어서', '토큰 아끼는 법']
    words = normalized.split()
    lines: list[str] = []
    current = ''
    for word in words:
        trial = f'{current} {word}'.strip()
        if len(trial) > 13 and current:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return lines[:3] or [normalized]


def _paper_noise(image: Image.Image, seed: int, amount: int = 1800, color=(40, 40, 40)) -> None:
    rng = random.Random(seed)
    draw = ImageDraw.Draw(image)
    for _ in range(amount):
        x = rng.randrange(image.width)
        y = rng.randrange(image.height)
        alpha = rng.choice((7, 9, 11, 14))
        base = image.getpixel((x, y))
        mixed = tuple(int((base[i] * (255 - alpha) + color[i] * alpha) / 255) for i in range(3))
        draw.point((x, y), fill=mixed)


def _draw_title(
    draw: ImageDraw.ImageDraw, lines: list[str], xy: tuple[int, int], *,
    max_width: int, size: int, fill: str, accent: str | None = None,
    accent_line: int = 2, kind: str = 'sans', spacing: int = 10,
    override: str | None = None,
) -> int:
    x, y = xy
    for line_index, line in enumerate(lines):
        font = _fit_font(draw, line, kind, size, max_width, override)
        draw.text((x, y), line, font=font, fill=accent if accent and line_index == accent_line else fill)
        box = draw.textbbox((x, y), line, font=font)
        y = box[3] + spacing
    return y


def _small(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, fill: str, *,
           size: int = 22, kind: str = 'sans', anchor: str | None = None) -> None:
    draw.text(xy, text, font=_font(kind, size), fill=fill, anchor=anchor)


def _screen(
    draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], *,
    dark: bool = False, chat: bool = False, border: str = '#15171B', radius: int = 18,
) -> None:
    x1, y1, x2, y2 = box
    fill = '#111822' if dark else '#FAFBFD'
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=border, width=4)
    draw.line((x1, y1 + 52, x2, y1 + 52), fill=border, width=3)
    for i, color in enumerate(('#E66F72', '#F2C75C', '#5CCB8B')):
        cx = x1 + 28 + i * 28
        draw.ellipse((cx - 7, y1 + 19, cx + 7, y1 + 33), fill=color)
    if chat:
        palette = ('#DDE5FF', '#DDF4EB', '#F3E2EA')
        for row in range(4):
            width = int((x2 - x1) * (0.52 if row % 2 == 0 else 0.40))
            left = x1 + 44 if row % 2 == 0 else x2 - width - 44
            top = y1 + 92 + row * 76
            draw.rounded_rectangle((left, top, left + width, top + 48), radius=14, fill=palette[row % 3])
    else:
        colors = ('#69D6B6', '#7296F4', '#E48AA9', '#93A1B6')
        usable = x2 - x1 - 105
        for row in range(8):
            top = y1 + 86 + row * 38
            lead = (row % 3) * 24
            length = int(usable * (0.42 + (row % 4) * 0.12))
            draw.rounded_rectangle((x1 + 48 + lead, top, x1 + 48 + lead + length, top + 10), radius=5,
                                   fill=colors[row % len(colors)])


def _shadowed_panel(image: Image.Image, box: tuple[int, int, int, int], fill: str, radius: int = 18) -> None:
    x1, y1, x2, y2 = box
    shadow = Image.new('RGBA', image.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle((x1 + 12, y1 + 18, x2 + 12, y2 + 18), radius=radius, fill=(0, 0, 0, 45))
    shadow = shadow.filter(ImageFilter.GaussianBlur(12))
    image.paste(shadow, (0, 0), shadow)
    ImageDraw.Draw(image).rounded_rectangle(box, radius=radius, fill=fill)


def _metal_ring(image: Image.Image, box: tuple[int, int, int, int]) -> None:
    layer = Image.new('RGBA', image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    colors = ('#4B5361', '#EFF3F8', '#8B95A5', '#FFFFFF', '#66707F')
    for width, color in zip((70, 54, 38, 22, 10), colors):
        draw.ellipse(box, outline=color, width=width)
    layer = layer.rotate(-18, resample=Image.Resampling.BICUBIC, center=((box[0] + box[2]) // 2, (box[1] + box[3]) // 2))
    image.paste(layer, (0, 0), layer)


def _header(draw: ImageDraw.ImageDraw, index: int, name: str, ink: str, *, inverse: bool = False) -> None:
    color = '#FFFFFF' if inverse else ink
    _small(draw, (54, 44), f'{index:02d}', color, size=24, kind='mono')
    _small(draw, (112, 44), name, color, size=22, kind='sans')


def render_concept_cover(
    design_language: str, index: int, title: str = DEFAULT_TITLE,
    subtitle: str = DEFAULT_SUBTITLE, font_path: str | None = None,
) -> Image.Image:
    if design_language not in SUPPORTED_DESIGN_LANGUAGES:
        raise ValueError(f'Unsupported design language: {design_language}')
    lines = _title_lines(title)
    name = design_language

    if name == 'Swiss Typography':
        im = Image.new('RGB', (WIDTH, HEIGHT), '#FFFFFF'); d = ImageDraw.Draw(im)
        _header(d, index, name, '#111111')
        d.line((54, 94, 1026, 94), fill='#111111', width=2)
        y = _draw_title(d, lines, (54, 190), max_width=920, size=112, fill='#0B0B0D', accent='#205FE6', accent_line=0, spacing=-3)
        d.rectangle((54, y + 12, 630, y + 25), fill='#205FE6')
        _small(d, (54, y + 58), subtitle, '#111111', size=36)
        _small(d, (54, 1235), 'SMARTER DEVELOPMENT / A BRIGHTER TOMORROW', '#111111', size=16, kind='mono')
        _small(d, (1026, 1235), '2026', '#111111', size=16, kind='mono', anchor='ra')

    elif name == 'Retro Tech UI':
        im = Image.new('RGB', (WIDTH, HEIGHT), '#BFC1BD'); d = ImageDraw.Draw(im)
        _header(d, index, name, '#111111')
        d.rectangle((66, 112, 1014, 1250), fill='#D6D6D2', outline='#111111', width=6)
        d.rectangle((66, 112, 1014, 170), fill='#2254D9', outline='#111111', width=5)
        _small(d, (92, 128), 'CODING_BETTER.EXE', '#FFFFFF', size=24, kind='mono')
        for x in (888, 934, 978): d.rectangle((x, 126, x + 24, 152), fill='#D6D6D2', outline='#111111', width=3)
        d.rectangle((104, 206, 790, 820), fill='#E8E8E4', outline='#111111', width=5)
        _draw_title(d, lines, (136, 274), max_width=610, size=79, fill='#101010', spacing=4, kind='mono')
        d.rectangle((132, 635, 584, 704), fill='#71F06B', outline='#111111', width=3)
        _small(d, (154, 650), subtitle, '#101010', size=33, kind='mono')
        d.line((820, 206, 820, 1170), fill='#111111', width=4)
        for y, label in ((268, 'CODEX + CHAT'), (500, 'LESS TOKENS'), (732, 'QUICK MODE')):
            d.rectangle((856, y, 966, y + 86), outline='#111111', width=4)
            _small(d, (910, y + 108), label, '#111111', size=16, kind='mono', anchor='ma')
        _paper_noise(im, index, 2600)

    elif name == 'Bento Editorial':
        im = Image.new('RGB', (WIDTH, HEIGHT), '#EEF1F5'); d = ImageDraw.Draw(im)
        _header(d, index, name, '#18202B')
        cells = [((54, 112, 470, 382), '#FFFFFF'), ((494, 112, 1026, 500), '#C8DCF5'),
                 ((54, 406, 690, 1080), '#D7E6FA'), ((714, 524, 1026, 1080), '#FFFFFF'),
                 ((54, 1104, 1026, 1280), '#D8E4F2')]
        for box, color in cells: d.rounded_rectangle(box, radius=28, fill=color)
        _small(d, (88, 152), 'DEVELOP\nFASTER\nTHINK\nDEEPER', '#18202B', size=26, kind='mono')
        d.rounded_rectangle((640, 196, 836, 392), radius=36, fill='#1B2430')
        _small(d, (738, 294), '</>', '#FFFFFF', size=58, kind='mono', anchor='mm')
        d.rounded_rectangle((824, 248, 978, 402), radius=32, fill='#FFFFFF')
        _small(d, (901, 325), 'AI', '#2457C7', size=48, kind='sans', anchor='mm')
        _draw_title(d, lines, (86, 486), max_width=560, size=77, fill='#151A22', accent='#2457C7', accent_line=2, spacing=5)
        _small(d, (88, 912), subtitle, '#151A22', size=33)
        for j, text in enumerate(('Codex', 'ChatGPT', 'Less Tokens')):
            _small(d, (752, 624 + j * 92), f'0{j + 1}  {text}', '#202936', size=23, kind='mono')
        _small(d, (874, 1188), 'BETTER TOOLS\nBRIGHTER YOU', '#202936', size=18, kind='mono', anchor='mm')

    elif name == 'Monochrome Magazine':
        im = Image.new('RGB', (WIDTH, HEIGHT), '#111111'); d = ImageDraw.Draw(im)
        _header(d, index, name, '#FFFFFF', inverse=True)
        _small(d, (1026, 44), '2026 / VOL. 01', '#FFFFFF', size=18, kind='mono', anchor='ra')
        _draw_title(d, lines, (82, 222), max_width=890, size=91, fill='#F7F7F4', kind='serif', spacing=0)
        d.line((82, 614, 710, 614), fill='#FFFFFF', width=3)
        _small(d, (84, 642), subtitle, '#FFFFFF', size=31, kind='serif')
        d.polygon([(0, 930), (1080, 724), (1080, 1350), (0, 1350)], fill='#D9D9D6')
        for x in range(-120, 1200, 170):
            d.polygon([(x, 1050), (x + 92, 1032), (x + 250, 1350), (x + 158, 1350)], fill='#505050')
        _small(d, (994, 1178), 'BETTER\nDEVELOPERS\nBRIGHTER\nTOMORROW', '#111111', size=18, kind='mono', anchor='mm')
        _paper_noise(im, index, 3200, (230, 230, 230))

    elif name == 'Neo Brutalism':
        im = Image.new('RGB', (WIDTH, HEIGHT), '#FFF43B'); d = ImageDraw.Draw(im)
        _header(d, index, name, '#111111')
        d.rectangle((52, 112, 1028, 1278), fill='#FFF43B', outline='#111111', width=8)
        d.rectangle((82, 192, 986, 590), fill='#FFFDF0', outline='#111111', width=8)
        _draw_title(d, lines, (108, 232), max_width=820, size=86, fill='#111111', spacing=-2)
        d.rectangle((88, 616, 716, 704), fill='#FF7DD0', outline='#111111', width=7)
        _small(d, (118, 634), subtitle, '#111111', size=42)
        for box, color, label, angle in [((104, 794, 490, 1088), '#111111', '>_\nCodex', -7),
                                          ((570, 754, 946, 1062), '#33D199', 'AI\nChat', 7)]:
            key = Image.new('RGBA', (430, 350), (0, 0, 0, 0)); kd = ImageDraw.Draw(key)
            kd.rounded_rectangle((20, 20, 410, 330), radius=42, fill=color, outline='#111111', width=8)
            kd.multiline_text((215, 175), label, font=_font('sans', 54), fill='#FFFFFF' if color == '#111111' else '#111111', anchor='mm', align='center', spacing=4)
            key = key.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC)
            im.paste(key, (box[0], box[1]), key)
        d.line((496, 912, 570, 890), fill='#111111', width=12)
        d.polygon([(566, 870), (614, 884), (578, 918)], fill='#111111')

    elif name == 'Japanese Editorial':
        im = Image.new('RGB', (WIDTH, HEIGHT), '#F5F4EF'); d = ImageDraw.Draw(im)
        _header(d, index, name, '#191919')
        d.line((80, 136, 790, 136), fill='#2D2D2D', width=2)
        _small(d, (82, 158), 'AI WORKFLOW / CARD NEWS', '#555555', size=16, kind='mono')
        _draw_title(d, lines, (82, 260), max_width=760, size=72, fill='#171717', kind='serif', spacing=4)
        _small(d, (84, 566), subtitle, '#363636', size=28, kind='serif')
        d.rectangle((0, 742, 1080, 1350), fill='#E7E5DF')
        d.polygon([(130, 1110), (850, 840), (1010, 1090), (292, 1348)], fill='#CFCBC3')
        d.ellipse((274, 780, 592, 1098), fill='#383838')
        d.ellipse((558, 674, 850, 966), fill='#A9A8A5')
        _small(d, (938, 202), 'AI', '#252525', size=66, kind='serif', anchor='ma')
        _small(d, (964, 332), '良\nい\n道\n具\nは\n、\n余\n白\nを\n作\nる\n。', '#252525', size=19, kind='serif', anchor='ma')
        _paper_noise(im, index, 2200)

    elif name == 'Quiet Luxury Editorial':
        im = Image.new('RGB', (WIDTH, HEIGHT), '#E8E2D9'); d = ImageDraw.Draw(im)
        _header(d, index, name, '#403B36')
        _draw_title(d, lines, (84, 238), max_width=840, size=76, fill='#2C2926', kind='serif', spacing=7)
        _small(d, (86, 560), subtitle, '#4B443E', size=27, kind='serif')
        d.rectangle((0, 720, 1080, 1350), fill='#D8D0C5')
        d.polygon([(40, 1350), (590, 788), (1080, 918), (1080, 1350)], fill='#BFB6AA')
        _shadowed_panel(im, (170, 900, 450, 1178), '#252422', 34)
        _shadowed_panel(im, (480, 870, 760, 1148), '#F2EEE8', 34)
        d = ImageDraw.Draw(im)
        _small(d, (310, 1038), '>_', '#FFFFFF', size=52, kind='mono', anchor='mm')
        _small(d, (620, 1008), 'AI', '#5C554F', size=56, anchor='mm')
        _small(d, (86, 1260), 'LESS TOKENS / MORE MEANINGFUL WORK', '#4B443E', size=16, kind='mono')
        _paper_noise(im, index, 2200)

    elif name == 'Newspaper 2.0':
        im = Image.new('RGB', (WIDTH, HEIGHT), '#F7F6F2'); d = ImageDraw.Draw(im)
        _header(d, index, name, '#111111')
        d.line((52, 96, 1028, 96), fill='#111111', width=6); d.line((52, 112, 1028, 112), fill='#111111', width=2)
        _small(d, (54, 132), 'AI WORKFLOW NEWS', '#111111', size=18, kind='mono')
        _small(d, (1026, 132), '2026. 09. 18 / VOL. 217', '#111111', size=16, kind='mono', anchor='ra')
        _draw_title(d, lines, (54, 202), max_width=910, size=79, fill='#111111', spacing=-2)
        _small(d, (56, 514), subtitle, '#222222', size=30, kind='serif')
        d.line((52, 572, 1028, 572), fill='#111111', width=3)
        _screen(d, (54, 610, 1026, 1068), dark=True)
        d.rectangle((760, 610, 1026, 1068), fill='#E9E9E6')
        _small(d, (788, 654), '작은 설정이\n더 큰 가능성을\n만듭니다.', '#111111', size=29)
        d.rectangle((784, 852, 1002, 934), fill='#91F083')
        _small(d, (802, 872), 'SMART IDEAS\nREAL IMPACT', '#111111', size=18, kind='mono')
        for x in (54, 374, 694): d.line((x, 1098, x, 1274), fill='#777777', width=1)
        _paper_noise(im, index, 2600)

    elif name == 'Technical Manual':
        im = Image.new('RGB', (WIDTH, HEIGHT), '#F7FAFC'); d = ImageDraw.Draw(im)
        for x in range(0, WIDTH, 54): d.line((x, 0, x, HEIGHT), fill='#DFE8EF', width=1)
        for y in range(0, HEIGHT, 54): d.line((0, y, WIDTH, y), fill='#DFE8EF', width=1)
        _header(d, index, name, '#17334C')
        _small(d, (1026, 44), '/ SYSTEM GUIDE', '#557088', size=17, kind='mono', anchor='ra')
        _draw_title(d, lines, (78, 180), max_width=880, size=78, fill='#101820', accent='#2268C4', accent_line=2, spacing=2)
        _small(d, (80, 506), subtitle, '#21394E', size=27, kind='mono')
        d.line((98, 820, 518, 654), fill='#315B7C', width=5); d.line((518, 654, 864, 808), fill='#315B7C', width=5)
        d.polygon([(98, 820), (518, 962), (518, 654)], outline='#315B7C'); d.polygon([(518, 962), (864, 808), (518, 654)], outline='#315B7C')
        d.line((98, 820, 518, 962), fill='#315B7C', width=5); d.line((518, 962, 864, 808), fill='#315B7C', width=5)
        d.rectangle((168, 672, 442, 874), outline='#315B7C', width=5)
        d.rectangle((644, 672, 804, 874), outline='#315B7C', width=5)
        d.line((162, 628, 448, 628), fill='#2268C4', width=2); d.line((162, 610, 162, 646), fill='#2268C4', width=2); d.line((448, 610, 448, 646), fill='#2268C4', width=2)
        _small(d, (305, 594), 'CODEX WORKSPACE', '#2268C4', size=16, kind='mono', anchor='ma')
        _small(d, (724, 900), 'QUICK CHAT', '#2268C4', size=16, kind='mono', anchor='ma')

    elif name == 'Screenshot Editorial':
        im = Image.new('RGB', (WIDTH, HEIGHT), '#F7F8FA'); d = ImageDraw.Draw(im)
        _header(d, index, name, '#141820')
        _screen(d, (72, 116, 1008, 744), chat=True)
        d.rectangle((72, 116, 316, 744), fill='#EDF1F8')
        for j, label in enumerate(('Codex', 'Quick Chat', 'Files', 'Terminal', 'Deploy')):
            if j == 1: d.rounded_rectangle((94, 242, 292, 300), radius=14, fill='#DCE5FF')
            _small(d, (116, 164 + j * 84), label, '#202936', size=24)
        _draw_title(d, lines, (72, 826), max_width=870, size=78, fill='#131820', accent='#2268D8', accent_line=2, spacing=0)
        _small(d, (74, 1154), subtitle, '#232A33', size=29)
        _small(d, (1026, 1248), 'SAVE TOKENS / DO MORE', '#697386', size=15, kind='mono', anchor='ra')

    elif name == 'Prompt Playground':
        im = Image.new('RGB', (WIDTH, HEIGHT), '#F0ECFA'); d = ImageDraw.Draw(im)
        _header(d, index, name, '#2A2142')
        _screen(d, (74, 184, 672, 690), dark=True)
        _screen(d, (330, 330, 1004, 902), chat=True)
        d.rounded_rectangle((526, 152, 918, 284), radius=26, fill='#FFFFFF', outline='#B7A7E8', width=3)
        _small(d, (560, 186), '/quick', '#6645C8', size=27, kind='mono')
        d.polygon([(870, 284), (910, 340), (886, 332), (874, 374), (858, 366)], fill='#111111')
        d.rectangle((74, 690, 338, 774), fill='#FFF1A8')
        _small(d, (94, 710), 'Quick Chat를\n작업별로 분리', '#2A2142', size=21)
        _draw_title(d, lines, (76, 946), max_width=930, size=68, fill='#211A31', accent='#5C5FE8', accent_line=2, spacing=0)
        _small(d, (78, 1218), subtitle, '#2A2142', size=28)

    elif name == 'Terminal Noir':
        im = Image.new('RGB', (WIDTH, HEIGHT), '#070B0E'); d = ImageDraw.Draw(im)
        _header(d, index, name, '#EAF7EF', inverse=True)
        _small(d, (1026, 44), '/ DARK MODE', '#80968D', size=16, kind='mono', anchor='ra')
        _draw_title(d, lines, (74, 220), max_width=920, size=76, fill='#F1F7F4', accent='#48D6A4', accent_line=2, kind='mono', spacing=4)
        _small(d, (76, 546), f'> {subtitle} ▮', '#48D6A4', size=28, kind='mono')
        d.rectangle((68, 638, 1012, 1106), outline='#31433D', width=3)
        d.line((540, 638, 540, 1106), fill='#31433D', width=3)
        for left, label, color in ((96, '[ CODEX ]', '#48D6A4'), (572, '[ CHATGPT ]', '#66A7F3')):
            _small(d, (left, 688), label, color, size=22, kind='mono')
            for row, text in enumerate(('> project', '> files/', '> docs/', '> quick chat')):
                _small(d, (left + 12, 754 + row * 62), text, '#A9B7B2', size=20, kind='mono')
        _small(d, (74, 1246), '$ BUILD A BETTER TOMORROW', '#48D6A4', size=16, kind='mono')

    elif name == 'Fluorescent Minimal':
        im = Image.new('RGB', (WIDTH, HEIGHT), '#FFFFFF'); d = ImageDraw.Draw(im)
        _header(d, index, name, '#121212')
        _small(d, (1026, 44), '/ COVER CONCEPT', '#707070', size=15, kind='mono', anchor='ra')
        y = _draw_title(d, lines, (56, 262), max_width=880, size=91, fill='#101010', spacing=-2)
        d.rectangle((52, y + 3, 782, y + 24), fill='#B8FF28')
        d.rounded_rectangle((54, y + 76, 472, y + 154), radius=39, fill='#B8FF28')
        _small(d, (263, y + 115), subtitle, '#101010', size=29, anchor='mm')
        d.line((810, 510, 938, 428), fill='#B8FF28', width=10); d.line((846, 558, 990, 510), fill='#B8FF28', width=10)
        _small(d, (802, 360), 'SAME AI\nSMARTER YOU.', '#9AD900', size=26, kind='mono')
        _small(d, (56, 1240), 'LESS TOKENS / MORE PROGRESS', '#4C4C4C', size=15, kind='mono')

    elif name == 'Soft Swiss':
        im = Image.new('RGB', (WIDTH, HEIGHT), '#FCFBFA'); d = ImageDraw.Draw(im)
        d.rectangle((746, 0, 1080, 1350), fill='#DDEBFA')
        _header(d, index, name, '#1D2230')
        _draw_title(d, lines, (66, 248), max_width=760, size=84, fill='#141820', accent='#3976CC', accent_line=1, spacing=0)
        _small(d, (70, 596), subtitle, '#2B3342', size=29)
        _shadowed_panel(im, (612, 734, 1020, 1012), '#FFFFFF', 28)
        d = ImageDraw.Draw(im)
        for j, label in enumerate(('Codex', 'ChatGPT', 'Quick Chat')):
            color = ('#161C26', '#36B99B', '#8797EC')[j]
            d.rounded_rectangle((650, 772 + j * 70, 692, 814 + j * 70), radius=11, fill=color)
            _small(d, (720, 780 + j * 70), label, '#303746', size=22)
        d.polygon([(924, 1002), (958, 1040), (938, 1036), (930, 1078), (914, 1072)], fill='#111111')
        _small(d, (812, 1124), '좋은 도구는\n더 멀리 가게 해줍니다.', '#566073', size=18, anchor='mm')

    elif name == 'Index / Catalogue':
        im = Image.new('RGB', (WIDTH, HEIGHT), '#EEEDE8'); d = ImageDraw.Draw(im)
        _header(d, index, name, '#111111')
        d.line((50, 94, 1030, 94), fill='#111111', width=2)
        for x, text in ((54, '01 TOOLS'), (270, '02 WORKFLOW'), (548, '03 PRODUCTIVITY')):
            _small(d, (x, 112), text, '#333333', size=15, kind='mono')
        d.line((50, 162, 1030, 162), fill='#111111', width=2)
        _draw_title(d, lines, (60, 232), max_width=710, size=78, fill='#121212', spacing=0)
        _small(d, (62, 542), subtitle, '#222222', size=27)
        d.line((790, 190, 790, 674), fill='#111111', width=2)
        _small(d, (834, 246), 'A\nSMALL\nCHAT\nA\nBIGGER\nFLOW', '#333333', size=20, kind='mono')
        d.rectangle((0, 714, 1080, 1350), fill='#181A1E')
        _screen(d, (84, 790, 734, 1188), dark=True)
        d.ellipse((790, 850, 1008, 1068), fill='#E8E4DD')
        _small(d, (899, 959), 'BETTER\nTOOLS', '#303030', size=24, anchor='mm')
        _small(d, (64, 1292), 'CODEX × CHATGPT', '#F5F5F5', size=16, kind='mono')

    elif name == 'Cinematic Title Card':
        im = Image.new('RGB', (WIDTH, HEIGHT), '#101316'); d = ImageDraw.Draw(im)
        _header(d, index, name, '#F4F1EB', inverse=True)
        d.rectangle((0, 820, 1080, 1350), fill='#090B0D')
        d.ellipse((760, 640, 1140, 1120), fill='#1D2822')
        d.rectangle((118, 848, 838, 884), fill='#2B211C')
        d.polygon([(304, 626), (806, 648), (730, 934), (366, 914)], fill='#171C24')
        d.line((318, 654, 788, 672), fill='#48505C', width=4)
        _draw_title(d, lines, (72, 236), max_width=880, size=82, fill='#F4F1EB', kind='serif', spacing=5)
        d.line((72, 588, 650, 588), fill='#D8C1A8', width=2)
        _small(d, (74, 620), subtitle, '#E8DDD2', size=27, kind='serif')
        _small(d, (74, 1246), '좋은 도구는 생각을 방해하지 않는다.', '#A9A39C', size=18, kind='serif')
        _paper_noise(im, index, 2800, (230, 225, 215))

    elif name == 'Zine Collage':
        im = Image.new('RGB', (WIDTH, HEIGHT), '#D9D9D4'); d = ImageDraw.Draw(im)
        _header(d, index, name, '#171717')
        d.polygon([(64, 214), (978, 174), (1008, 618), (38, 660)], fill='#F4F1E9')
        d.rectangle((104, 174, 288, 218), fill='#171717'); d.rectangle((806, 578, 970, 620), fill='#171717')
        _draw_title(d, lines, (94, 268), max_width=820, size=80, fill='#141414', kind='serif', spacing=-2)
        d.rectangle((72, 700, 1000, 1174), fill='#BDBDB8')
        _screen(d, (118, 756, 720, 1108), dark=True)
        d.polygon([(0, 964), (286, 850), (402, 1350), (0, 1350)], fill='#F2F0E9')
        d.polygon([(778, 728), (1080, 682), (1080, 1124), (844, 1152)], fill='#C7E0D5')
        _small(d, (920, 866), '작은 팁이\n큰 시간을\n아낀다.', '#232323', size=24, kind='serif', anchor='mm')
        _small(d, (92, 1208), subtitle, '#171717', size=26, kind='serif')
        _paper_noise(im, index, 4400)

    elif name == 'Split Screen':
        im = Image.new('RGB', (WIDTH, HEIGHT), '#F9FAFC'); d = ImageDraw.Draw(im)
        d.rectangle((0, 0, 540, 1350), fill='#111821')
        _header(d, index, name, '#FFFFFF', inverse=True)
        _small(d, (1026, 44), 'TWO TOOLS / ONE FLOW', '#4B5566', size=15, kind='mono', anchor='ra')
        _small(d, (270, 286), 'Codex', '#F1F4F8', size=60, kind='mono', anchor='mm')
        _small(d, (810, 286), 'ChatGPT', '#202632', size=58, anchor='mm')
        d.line((540, 86, 540, 1350), fill='#8C97A9', width=3)
        _screen(d, (70, 432, 492, 830), dark=True)
        _screen(d, (588, 432, 1010, 830), chat=True)
        d.ellipse((512, 352, 568, 408), fill='#FFFFFF', outline='#687386', width=3)
        _small(d, (540, 380), '×', '#303746', size=28, anchor='mm')
        d.rectangle((126, 916, 954, 1188), fill='#FFFFFF')
        _draw_title(d, lines, (170, 948), max_width=740, size=66, fill='#11151C', accent='#2DAE8E', accent_line=1, spacing=0)
        _small(d, (540, 1236), subtitle, '#303746', size=25, anchor='ma')

    elif name == 'Chrome Accent':
        im = Image.new('RGB', (WIDTH, HEIGHT), '#F4F5F7'); d = ImageDraw.Draw(im)
        _header(d, index, name, '#252B34')
        _draw_title(d, lines, (84, 244), max_width=850, size=76, fill='#1D222A', accent='#467F73', accent_line=1, kind='serif', spacing=4)
        _small(d, (86, 568), subtitle, '#252B34', size=27, kind='serif')
        _metal_ring(im, (210, 742, 920, 1238))
        d = ImageDraw.Draw(im)
        _small(d, (922, 1192), 'CODEX\nCHATGPT', '#49515D', size=15, kind='mono', anchor='mm')
        _small(d, (90, 1218), 'A SMARTER WAY TO WORK', '#68717E', size=16, kind='mono')

    else:  # Modular Poster
        im = Image.new('RGB', (WIDTH, HEIGHT), '#F5F4F0'); d = ImageDraw.Draw(im)
        _header(d, index, name, '#111111')
        blocks = [((48, 112, 694, 328), '#FFFFFF'), ((694, 112, 1032, 328), '#D8E0DC'),
                  ((48, 328, 1032, 548), '#2CB58D'), ((48, 548, 704, 986), '#FFFFFF'),
                  ((704, 548, 1032, 986), '#111111'), ((48, 986, 360, 1302), '#D9DEDB'),
                  ((360, 986, 1032, 1302), '#FFFFFF')]
        for box, color in blocks: d.rectangle(box, fill=color, outline='#111111', width=3)
        _small(d, (732, 160), 'SAME\nTOOLS\nBIGGER\nPOSSIBILITIES', '#111111', size=20, kind='mono')
        _small(d, (868, 692), 'QUICK CHAT\n활용', '#FFFFFF', size=24, kind='mono', anchor='mm')
        first, second, third = (lines + ['', '', ''])[:3]
        d.text((74, 156), first, font=_fit_font(d, first, 'sans', 86, 580), fill='#111111')
        d.text((74, 348), second, font=_fit_font(d, second, 'sans', 82, 850), fill='#FFFFFF')
        d.text((74, 572), third, font=_fit_font(d, third, 'sans', 82, 580), fill='#111111')
        d.ellipse((484, 1040, 800, 1356), fill='#111111')
        _small(d, (642, 1186), 'Codex\n×\nChatGPT', '#FFFFFF', size=22, kind='mono', anchor='mm')
        _small(d, (92, 1092), 'CODE\nASK\nSOLVE\nFASTER', '#202020', size=18, kind='mono')

    return im


def _comparison_sheet(covers: list[Path], names: list[str], page: int) -> Image.Image:
    canvas = Image.new('RGB', (1800, 2300), '#EEF0F2')
    draw = ImageDraw.Draw(canvas)
    _small(draw, (70, 54), f'표지 컨셉 비교 · {page} / 5', '#1C222B', size=36)
    _small(draw, (1730, 62), 'SAME COPY / DIFFERENT DESIGN SYSTEMS', '#5E6877', size=18, kind='mono', anchor='ra')
    positions = ((70, 142), (920, 142), (70, 1210), (920, 1210))
    for path, name, (x, y) in zip(covers, names, positions):
        with Image.open(path) as source:
            thumb = source.convert('RGB').resize((810, 1012), Image.Resampling.LANCZOS)
        canvas.paste(thumb, (x, y))
        draw.rectangle((x, y, x + 810, y + 1012), outline='#C2C7CF', width=2)
        _small(draw, (x, y - 42), name, '#222831', size=23)
    return canvas


def _overview(covers: list[Path]) -> Image.Image:
    canvas = Image.new('RGB', (1760, 2180), '#E9ECEF')
    draw = ImageDraw.Draw(canvas)
    _small(draw, (50, 42), '20 DESIGN LANGUAGES · SAME COPY', '#151A22', size=38)
    thumb_w, thumb_h = 320, 400
    gap_x, gap_y = 24, 46
    for i, path in enumerate(covers):
        row, col = divmod(i, 5)
        x = 50 + col * (thumb_w + gap_x)
        y = 118 + row * (thumb_h + gap_y + 34)
        with Image.open(path) as source:
            thumb = source.convert('RGB').resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)
        canvas.paste(thumb, (x, y))
        _small(draw, (x, y + thumb_h + 9), f'{i + 1:02d}  {SUPPORTED_DESIGN_LANGUAGES[i]}', '#303742', size=15)
    return canvas


def render_design_exploration(
    title: str, out_dir: Path, subtitle: str = DEFAULT_SUBTITLE,
    font_path: str | None = None,
) -> dict[str, Any]:
    """Render twenty independent covers and five four-up comparison sheets."""
    title = ' '.join(str(title).split())
    subtitle = ' '.join(str(subtitle).split())
    if not title:
        raise ValueError('A fixed title is required for design exploration')
    if not subtitle:
        raise ValueError('A fixed subtitle is required for design exploration')
    out_dir = Path(out_dir)
    cover_dir = out_dir / 'covers'
    comparison_dir = out_dir / 'comparisons'
    cover_dir.mkdir(parents=True, exist_ok=True)
    comparison_dir.mkdir(parents=True, exist_ok=True)

    covers: list[Path] = []
    for index, language in enumerate(SUPPORTED_DESIGN_LANGUAGES, 1):
        image = render_concept_cover(language, index, title, subtitle, font_path)
        path = cover_dir / f'{index:02d}-{_slug(language)}.png'
        image.save(path, optimize=True)
        covers.append(path)

    comparisons: list[Path] = []
    for offset in range(0, 20, 4):
        page = offset // 4 + 1
        sheet = _comparison_sheet(
            covers[offset:offset + 4], list(SUPPORTED_DESIGN_LANGUAGES[offset:offset + 4]), page,
        )
        path = comparison_dir / f'comparison-{page:02d}.png'
        sheet.save(path, optimize=True)
        comparisons.append(path)

    overview = out_dir / 'overview-20.png'
    _overview(covers).save(overview, optimize=True)
    hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in covers]
    if len(covers) != 20 or len(set(hashes)) != 20 or len(comparisons) != 5:
        raise RuntimeError('DESIGN_EXPLORATION_INCOMPLETE')
    manifest = {
        'schema_version': 1,
        'mode': 'concept_exploration',
        'fixed_copy': {'title': title, 'subtitle': subtitle},
        'selection_required_before_production': True,
        'covers': [
            {'index': i, 'design_language': language, 'file': str(path.relative_to(out_dir)), 'sha256': sha}
            for i, (language, path, sha) in enumerate(zip(SUPPORTED_DESIGN_LANGUAGES, covers, hashes), 1)
        ],
        'comparison_sheets': [str(path.relative_to(out_dir)) for path in comparisons],
        'overview': str(overview.relative_to(out_dir)),
    }
    manifest_path = out_dir / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return {
        'covers': covers,
        'comparison_sheets': comparisons,
        'overview': overview,
        'manifest': manifest_path,
    }
