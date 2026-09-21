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


def _scene_prompt(card: dict[str, Any], index: int) -> str:
    items = card.get('items') or []
    item_text = '; '.join(
        f"{x.get('label', '')}: {x.get('detail', '')}" for x in items if isinstance(x, dict)
    )
    return (
        'Create one square editorial lifestyle image for a Korean home-appliance help article. '
        f'Card role: {ROLES[index - 1]}. Topic: {card.get("headline", "")}. '
        f'Explanation: {card.get("copy", "")}. Visible situation and objects: {item_text}. '
        'Show a believable unbranded Korean home interior and concrete relevant appliances, '
        'containers, controls, filters, documents, measurements, or actions. '
        'The image must explain the situation visually, with a clear subject and natural scale. '
        'Bright neutral daylight, cool white, pale lavender, muted mint and soft blue accents; '
        'clean editorial photography with gentle realistic texture. '
        'Leave calm low-detail negative space across the upper 30 percent for a later typography overlay. '
        'No people unless hands are essential to demonstrate the action. '
        'ABSOLUTELY NO text, letters, numbers, logos, labels, UI glyphs, watermark, yellow cast, sepia, '
        'collage, floating icons, generic infographic nodes, or repeated template boxes.'
    )


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
) -> list[Path]:
    if len(cards) != 5:
        raise ValueError('Blog card set must contain exactly five cards')
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[Path] = []
    for index, card in enumerate(cards, 1):
        if budget:
            budget.reserve('image_generation', estimated_cost_usd, {
                'model': model, 'quality': quality, 'asset': 'blog_card_background',
                'card': index,
            })
        response = client.images.generate(
            model=model,
            prompt=_scene_prompt(card, index),
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

        final_path = out_dir / f'card_{index:02d}.png'
        canvas.convert('RGB').save(final_path, quality=95)
        results.append(final_path)
    return results
