from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .card_design import get_design_blueprint, get_design_language


ROLES = ('먼저 답', '왜 그런지', '조건 비교', '지금 확인', '마지막 판단')
ROLE_KEYS = ('cover', 'flow', 'comparison', 'checklist', 'decision')
ROLE_SCENE_DIRECTIONS = (
    'Show the real problem situation immediately, with one unmistakable main household object.',
    'Show the physical cause or action as a clear before-to-after sequence inside one believable scene.',
    'Show two real conditions at the same scale and viewing angle so the difference is physically visible.',
    'Show a hand performing the exact inspection action on the real object; avoid symbolic checkboxes.',
    'Show the final safe condition or decision using the real object and its surrounding context.',
)
EXPLORATION_ONLY_DESIGN_LANGUAGES = frozenset({
    'Retro Tech UI', 'Screenshot Editorial', 'Prompt Playground', 'Terminal Noir',
})


def production_design_language(name: str | None) -> str:
    """Keep technology-themed cover exploration out of real blog production."""
    if not name or name in EXPLORATION_ONLY_DESIGN_LANGUAGES:
        return 'Bento Editorial'
    return name


def _font(path: str | None, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [path, '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc',
                  '/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf']
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def _fit(draw: ImageDraw.ImageDraw, text: str, path: str | None, maximum: int,
         box: tuple[int, int, int, int], *, minimum: int = 24) -> ImageFont.ImageFont:
    x1, y1, x2, y2 = box
    for size in range(maximum, minimum - 1, -2):
        font = _font(path, size)
        left, top, right, bottom = draw.multiline_textbbox((0, 0), text, font=font, spacing=8)
        if right - left <= x2 - x1 and bottom - top <= y2 - y1:
            return font
    raise ValueError('Korean overlay text does not fit its reserved region')


def _subject_lock(card: dict[str, Any]) -> str:
    items = card.get('items') or []
    source = ' '.join([
        str(card.get('headline') or ''), str(card.get('copy') or ''),
        *(
            f"{item.get('label', '')} {item.get('detail', '')}"
            for item in items if isinstance(item, dict)
        ),
    ])
    locks = (
        (('노트북', 'RAM', '램', '발열'), 'Show a real open laptop on a clean desk with its ventilation path, cooling fan area, bottom intake grille, memory module or system monitoring context as required by the card. When RAM is mentioned, show a realistic laptop memory module and slot only if the action calls for it. Never show source code, terminal windows, Codex, ChatGPT, AI logos, abstract app UI, floating icons or generic technology cubes.'),
        (('고무패킹', '문틈', '곰팡이'), 'Show a close, unmistakable view of a household refrigerator door gasket: the flexible folded rubber seal around the door edge, its narrow groove, visible moisture or small mold spots, a soft cleaning cloth, and a clear comparison between a seal that lies flat and one that is torn or lifted. Never show a washing machine, document, ruler, generic appliance icon or unrelated filter.'),
        (('배수호스', '실외기'), 'Show the exact home air-conditioner inspection subject named in the card: a wall-mounted indoor air conditioner connected to a real flexible drain hose, or an outdoor condenser unit with open airflow and no cover. When the card is about the drain hose, make the hose, bend, outlet and water path the main subject. Never replace them with a ruler, document, generic appliance or abstract icon.'),
        (('제습기', '물통'), 'Show a recognizable floor-standing home dehumidifier with its removable transparent water tank pulled out, remaining water droplets, the tank lid and a clean cloth or drying rack. Make emptying and fully air-drying the tank visually obvious. Never substitute an air purifier, humidifier, refrigerator, document or generic white appliance.'),
        (('식기세척기',), 'Show a built-in kitchen dishwasher with dish racks and a door-mounted detergent dispenser. Never show an air purifier, dehumidifier, water purifier, laundry washer or generic white appliance.'),
        (('세탁기',), 'Show a front-loading laundry washing machine with a circular drum door and the pull-out detergent drawer at the upper front. Never show dishwasher racks, spray arms or a kitchen dishwasher.'),
        (('건조기',), 'Show a front-loading tumble clothes dryer with a circular door and a removable lint filter at the door or lower opening. Never show an air purifier, dehumidifier or water tank appliance.'),
        (('와이파이', '2.4GHz'), 'Show only a recognizable wireless router with antennas, a smartphone or laptop, walls and signal-distance context. Never add household cleaning appliances, filters, brushes or vacuum parts.'),
        (('로봇청소기',), 'Show a low round robot vacuum and its dock, dust bin or clean-water tank. Never substitute an air purifier or dehumidifier.'),
        (('공기청정기',), 'Show a floor-standing air purifier with a large removable air filter and intake grille. Explain every point only through the physical purifier, filter condition, airflow, placement, and an inspecting hand. Never show a dehumidifier water tank, product packaging, printed report, test certificate, safety document, smartphone screen, control-panel text, label, model number, badge, seal, or any object that could contain writing.'),
        (('김치냉장고',), 'Show a Korean kimchi refrigerator with sealed kimchi containers, shelf position and cold-air outlet context. No generic document icons.'),
        (('냉장고',), 'Show a clearly recognizable household refrigerator and the exact part named by the card. If the topic concerns a door, seal or gasket, use a close-up of that physical part rather than the refrigerator interior. Never use a generic appliance, document or abstract icon.'),
        (('에어컨',), 'Show a clearly recognizable wall-mounted home air conditioner and the exact physical part or action named by the card, such as its mesh filter, flexible drain hose or outdoor condenser unit. Never use a generic appliance, document, ruler or abstract icon.'),
    )
    for keywords, instruction in locks:
        if any(keyword in source for keyword in keywords):
            return instruction
    return 'Keep the exact appliance category and action named by the topic; do not substitute a visually similar appliance.'


def _scene_prompt(
    card: dict[str, Any], index: int, revision_note: str = '', *,
    design_language: str = 'Bento Editorial', design_blueprint: dict[str, Any] | None = None,
) -> str:
    items = card.get('items') or []
    item_text = '; '.join(
        f"{x.get('label', '')}: {x.get('detail', '')}" for x in items if isinstance(x, dict)
    )
    # The cover explorer owns technology/UI motifs. Production backgrounds use
    # only the factual card role and real-life subject; typography is added later.
    role_key = ROLE_KEYS[index - 1]
    parts = [
        'Create one square editorial lifestyle image for a Korean home-appliance help article.',
        f'Card role: {ROLES[index - 1]}. Topic: {card.get("headline", "")}.',
        f'Explanation: {card.get("copy", "")}. Visible situation and objects: {item_text}.',
        _subject_lock(card),
        'Show a believable unbranded Korean home interior and concrete relevant appliances, containers, filters, measurements shown only by physical spacing or scale, and actions. Do not use documents, packaging, certificates, reports, phone screens, control-panel text or labels as visual evidence; all readable information will be typeset locally later.',
        'The image must explain the situation visually, with a clear subject and natural scale.',
        f'Production card role: {role_key}. {ROLE_SCENE_DIRECTIONS[index - 1]}',
        'Reference styling applies only to spacing, cool pastel accents and calm editorial composition. '
        'Do not copy cover-exploration motifs, software screens, code, terminals, chat windows or AI branding.',
        'Use a bright paper-white or cool-white editorial base with pale lavender, muted mint, soft blue and restrained coral accents. '
        'No beige, yellow cream, amber light or warm sepia cast.',
        'The concrete scene and objects must occupy roughly 65 to 72 percent of the frame and remain the first thing seen. '
        'Reserve one calm low-detail editorial zone in the lower 28 to 32 percent for later Korean typography.',
        'No people unless hands are essential to demonstrate the action.',
        'ABSOLUTELY NO text, letters, numbers, logos, labels, UI glyphs, watermark, yellow cast, beige cream, sepia, collage, source code, terminal, Codex, ChatGPT, AI branding, floating icons, generic infographic nodes, or repeated template boxes.',
    ]
    if revision_note:
        parts.append(
            'Mandatory correction for this retry. Do not repeat the rejected visual action or implication. '
            f'Use the article facts literally and correct this prior QA problem: {revision_note}'
        )
    return ' '.join(parts)


def generate_and_typeset_blog_cards(
    client: Any,
    cards: list[dict[str, Any]],
    out_dir: Path,
    *,
    model: str,
    quality: str,
    font_path: str | None,
    budget: Any | None = None,
    estimated_cost_usd: float = 0.05,
    only_indices: set[int] | None = None,
    revision_notes: dict[int, str] | None = None,
    design_language: str = 'Bento Editorial',
    design_blueprint: dict[str, Any] | None = None,
) -> list[Path]:
    if len(cards) != 5:
        raise ValueError('Blog card set must contain exactly five cards')
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[Path] = []
    revision_notes = revision_notes or {}
    design_language = production_design_language(design_language)
    style = get_design_language(design_language)
    # A caller-supplied exploration blueprint must never control generated scene
    # content. Rebuild the deterministic production typography contract instead.
    blueprint = get_design_blueprint(design_language)
    palette = tuple(style['palette'])
    accent = palette[(0 if design_language != 'Bento Editorial' else 1)]
    for index, card in enumerate(cards, 1):
        expected_layout = ROLE_KEYS[index - 1]
        actual_layout = str(card.get('layout') or expected_layout).strip().lower()
        if actual_layout != expected_layout:
            raise ValueError(
                f'Blog card {index} must use role {expected_layout!r}, got {actual_layout!r}'
            )
        final_path = out_dir / f'card_{index:02d}.png'
        if only_indices is not None and index not in only_indices:
            if not final_path.exists():
                raise RuntimeError(f'Missing preserved card for selective retry: {index}')
            results.append(final_path)
            continue
        if budget:
            budget.reserve('image_generation', estimated_cost_usd, {
                'model': model, 'quality': quality, 'asset': 'blog_card_background',
                'card': index,
            })
        response = client.images.generate(
            model=model,
            prompt=_scene_prompt(
                card, index, revision_notes.get(index, ''),
                design_language=design_language, design_blueprint=blueprint,
            ),
            size='1024x1024',
            quality=quality,
        )
        payload = getattr(response.data[0], 'b64_json', None)
        if not payload:
            raise RuntimeError('Image API returned no base64 image data')
        raw_path = out_dir / f'background_{index:02d}.png'
        raw_path.write_bytes(base64.b64decode(payload))

        background = Image.open(raw_path).convert('RGB')
        background = ImageOps.fit(background, (1080, 1080), method=Image.Resampling.LANCZOS)
        canvas = background.convert('RGBA')
        veil = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
        vd = ImageDraw.Draw(veil)
        # The scene stays dominant.  The selected named design language controls
        # the typography surface instead of every set receiving the old fixed
        # lavender header template.
        panel_y = 716
        panel_rgb = tuple(int(style['surface'][i:i + 2], 16) for i in (1, 3, 5))
        accent_rgb = tuple(int(accent[i:i + 2], 16) for i in (1, 3, 5))
        radius = min(int(style['radius']), 28)
        if style['panel'] in {'rule', 'sharp', 'split', 'modular'}:
            vd.rectangle((0, panel_y, 1080, 1080), fill=(*panel_rgb, 244))
            vd.rectangle((0, panel_y, 18, 1080), fill=(*accent_rgb, 255))
            vd.line((56, 812, 1024, 812), fill=(*accent_rgb, 210), width=max(2, int(style['border_width'])))
        else:
            vd.rounded_rectangle((30, panel_y, 1050, 1058), radius=radius, fill=(*panel_rgb, 244))
            vd.rounded_rectangle((58, 744, 258, 792), radius=min(radius, 18), fill=(*accent_rgb, 245))
        canvas = Image.alpha_composite(canvas, veil)
        draw = ImageDraw.Draw(canvas)

        headline = str(card.get('headline') or '').strip()
        copy = str(card.get('copy') or '').strip()
        role_font = _font(font_path, 23)
        role_fill = style['accent_text'] if style['panel'] not in {'rule', 'sharp', 'split', 'modular'} else accent
        role_x = 82 if style['panel'] not in {'rule', 'sharp', 'split', 'modular'} else 58
        draw.text((role_x, 754), ROLES[index - 1], font=role_font, fill=role_fill)
        draw.text((900, 754), f'{index:02d} / 05', font=_font(font_path, 22), fill='#202329')
        title_box = (58, 830, 1022, 930)
        copy_box = (58, 944, 1022, 1008)
        title_font = _fit(draw, headline, font_path, min(int(style['title_size']), 56), title_box, minimum=34)
        copy_font = _fit(draw, copy, font_path, min(int(style['copy_size']), 27), copy_box, minimum=21)
        draw.multiline_text((58, 830), headline, font=title_font, fill='#202329', spacing=8)
        draw.multiline_text((58, 944), copy, font=copy_font, fill='#343741', spacing=7)
        draw.text((58, 1020), 'AI 생성 설명 장면 · 실제 제품과 다를 수 있음',
                  font=_font(font_path, 19), fill='#565A63')

        canvas.convert('RGB').save(final_path, quality=95)
        results.append(final_path)
    return results
