from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps


ROLES = ('먼저 답', '왜 그런지', '조건 비교', '지금 확인', '마지막 판단')


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
        (('고무패킹', '문틈', '곰팡이'), 'Show a close, unmistakable view of a household refrigerator door gasket: the flexible folded rubber seal around the door edge, its narrow groove, visible moisture or small mold spots, a soft cleaning cloth, and a clear comparison between a seal that lies flat and one that is torn or lifted. Never show a washing machine, document, ruler, generic appliance icon or unrelated filter.'),
        (('배수호스', '실외기'), 'Show the exact home air-conditioner inspection subject named in the card: a wall-mounted indoor air conditioner connected to a real flexible drain hose, or an outdoor condenser unit with open airflow and no cover. When the card is about the drain hose, make the hose, bend, outlet and water path the main subject. Never replace them with a ruler, document, generic appliance or abstract icon.'),
        (('제습기', '물통'), 'Show a recognizable floor-standing home dehumidifier with its removable transparent water tank pulled out, remaining water droplets, the tank lid and a clean cloth or drying rack. Make emptying and fully air-drying the tank visually obvious. Never substitute an air purifier, humidifier, refrigerator, document or generic white appliance.'),
        (('식기세척기',), 'Show a built-in kitchen dishwasher with dish racks and a door-mounted detergent dispenser. Never show an air purifier, dehumidifier, water purifier, laundry washer or generic white appliance.'),
        (('세탁기',), 'Show a front-loading laundry washing machine with a circular drum door and the pull-out detergent drawer at the upper front. Never show dishwasher racks, spray arms or a kitchen dishwasher.'),
        (('건조기',), 'Show a front-loading tumble clothes dryer with a circular door and a removable lint filter at the door or lower opening. Never show an air purifier, dehumidifier or water tank appliance.'),
        (('와이파이', '2.4GHz'), 'Show only a recognizable wireless router with antennas, a smartphone or laptop, walls and signal-distance context. Never add household cleaning appliances, filters, brushes or vacuum parts.'),
        (('로봇청소기',), 'Show a low round robot vacuum and its dock, dust bin or clean-water tank. Never substitute an air purifier or dehumidifier.'),
        (('공기청정기',), 'Show a floor-standing air purifier with a large removable air filter and intake grille. Never show a dehumidifier water tank.'),
        (('김치냉장고',), 'Show a Korean kimchi refrigerator with sealed kimchi containers, shelf position and cold-air outlet context. No generic document icons.'),
        (('냉장고',), 'Show a clearly recognizable household refrigerator and the exact part named by the card. If the topic concerns a door, seal or gasket, use a close-up of that physical part rather than the refrigerator interior. Never use a generic appliance, document or abstract icon.'),
        (('에어컨',), 'Show a clearly recognizable wall-mounted home air conditioner and the exact physical part or action named by the card, such as its mesh filter, flexible drain hose or outdoor condenser unit. Never use a generic appliance, document, ruler or abstract icon.'),
    )
    for keywords, instruction in locks:
        if any(keyword in source for keyword in keywords):
            return instruction
    return 'Keep the exact appliance category and action named by the topic; do not substitute a visually similar appliance.'


def _scene_prompt(card: dict[str, Any], index: int, revision_note: str = '') -> str:
    items = card.get('items') or []
    item_text = '; '.join(
        f"{x.get('label', '')}: {x.get('detail', '')}" for x in items if isinstance(x, dict)
    )
    parts = [
        'Create one square editorial lifestyle image for a Korean home-appliance help article.',
        f'Card role: {ROLES[index - 1]}. Topic: {card.get("headline", "")}.',
        f'Explanation: {card.get("copy", "")}. Visible situation and objects: {item_text}.',
        _subject_lock(card),
        'Show a believable unbranded Korean home interior and concrete relevant appliances, containers, controls, filters, documents, measurements, or actions.',
        'The image must explain the situation visually, with a clear subject and natural scale.',
        'Bright neutral daylight, cool white, pale lavender, muted mint and soft blue accents; clean editorial photography with gentle realistic texture.',
        'Leave calm low-detail negative space across the upper 30 percent for a later typography overlay.',
        'No people unless hands are essential to demonstrate the action.',
        'ABSOLUTELY NO text, letters, numbers, logos, labels, UI glyphs, watermark, yellow cast, sepia, collage, floating icons, generic infographic nodes, or repeated template boxes.',
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
) -> list[Path]:
    if len(cards) != 5:
        raise ValueError('Blog card set must contain exactly five cards')
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[Path] = []
    revision_notes = revision_notes or {}
    for index, card in enumerate(cards, 1):
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
            prompt=_scene_prompt(card, index, revision_notes.get(index, '')),
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
        vd.rounded_rectangle((38, 34, 1042, 350), radius=28, fill=(250, 250, 252, 238))
        vd.rounded_rectangle((58, 54, 250, 102), radius=20, fill=(118, 103, 186, 238))
        vd.rounded_rectangle((38, 972, 1042, 1042), radius=22, fill=(250, 250, 252, 224))
        canvas = Image.alpha_composite(canvas, veil)
        draw = ImageDraw.Draw(canvas)

        headline = str(card.get('headline') or '').strip()
        copy = str(card.get('copy') or '').strip()
        role_font = _font(font_path, 24)
        draw.text((82, 65), ROLES[index - 1], font=role_font, fill='#FFFFFF')
        draw.text((900, 66), f'{index:02d} / 05', font=_font(font_path, 23), fill='#202329')
        title_box = (64, 126, 1016, 246)
        copy_box = (64, 264, 1016, 332)
        title_font = _fit(draw, headline, font_path, 52, title_box, minimum=34)
        copy_font = _fit(draw, copy, font_path, 26, copy_box, minimum=21)
        draw.multiline_text((64, 126), headline, font=title_font, fill='#202329', spacing=8)
        draw.multiline_text((64, 264), copy, font=copy_font, fill='#343741', spacing=7)
        draw.text((64, 994), '주제별 생성 장면 · 실제 제품과 다를 수 있음',
                  font=_font(font_path, 21), fill='#343741')

        canvas.convert('RGB').save(final_path, quality=95)
        results.append(final_path)
    return results
