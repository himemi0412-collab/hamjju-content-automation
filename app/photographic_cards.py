from __future__ import annotations

import base64
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .card_design import get_design_blueprint, get_design_language


ROLES = ('먼저 답', '왜 그런지', '조건 비교', '지금 확인', '마지막 판단')
ROLE_KEYS = ('cover', 'flow', 'comparison', 'checklist', 'decision')
ROLE_SCENE_DIRECTIONS = (
    'Use a candid close crop with the subject offset to the right and quiet negative space at the lower left. Show the real problem immediately with one unmistakable household object.',
    'Use an eye-level documentary view with the action running diagonally from lower left to upper right. Show the physical cause as one believable moment, not circular callouts or a diagram.',
    'Use a restrained straight-on split created by the two real conditions themselves at the same scale and viewpoint. Leave a quiet band only at the top; do not add arrows, badges, circles or dividers.',
    'Use an over-the-shoulder or hand-level inspection crop with the active hand and object low in frame and quiet negative space at the right. Avoid symbolic checkboxes and callout bubbles.',
    'Use a wider lived-in context with the resolved object left of center and natural empty wall or counter space in the lower right. Show exactly one next action; no icons, badges or approval symbols.',
)
EXPLORATION_ONLY_DESIGN_LANGUAGES = frozenset({
    'Retro Tech UI', 'Screenshot Editorial', 'Prompt Playground', 'Terminal Noir',
})


_REPO_ROOT = Path(__file__).resolve().parents[1]
_PRODUCTION_PROMPT_PATH = _REPO_ROOT / 'references' / 'card_news' / 'production_prompt_ko.md'
_PRODUCTION_CONTRACT_PATH = _REPO_ROOT / 'references' / 'card_news' / 'production_style_contract.json'


@lru_cache(maxsize=1)
def load_production_style_bundle() -> tuple[str, dict[str, Any]]:
    """Load the canonical v3 generation prompt and fail closed on drift or absence."""
    try:
        prompt = _PRODUCTION_PROMPT_PATH.read_text(encoding='utf-8').strip()
        contract = json.loads(_PRODUCTION_CONTRACT_PATH.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError('BLOG_CARD_PRODUCTION_STYLE_BUNDLE_UNAVAILABLE') from exc
    if not prompt or not str(contract.get('version', '')).endswith('-v3'):
        raise RuntimeError('BLOG_CARD_PRODUCTION_STYLE_BUNDLE_INVALID')
    qa = contract.get('qa') or {}
    if qa.get('ai_likeness_max_exclusive') != 5 or qa.get('fail_when_score_gte') != 5:
        raise RuntimeError('BLOG_CARD_AI_LIKENESS_GATE_INVALID')
    if contract.get('production_prompt') != 'references/card_news/production_prompt_ko.md':
        raise RuntimeError('BLOG_CARD_PRODUCTION_PROMPT_PATH_MISMATCH')
    return prompt, contract


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


def _wrap_to_width(draw: ImageDraw.ImageDraw, value: str,
                   font: ImageFont.ImageFont, width: int) -> str:
    lines: list[str] = []
    line = ''
    for char in value:
        candidate = line + char
        if line and draw.textbbox((0, 0), candidate, font=font)[2] > width:
            lines.append(line)
            line = char
        else:
            line = candidate
    if line:
        lines.append(line)
    return '\n'.join(lines)


def _subject_lock(card: dict[str, Any], index: int | None = None) -> str:
    items = card.get('items') or []
    source = ' '.join([
        str(card.get('headline') or ''), str(card.get('copy') or ''),
        *(
            f"{item.get('label', '')} {item.get('detail', '')}"
            for item in items if isinstance(item, dict)
        ),
    ])
    # The article topic wins over incidental words in individual card items.
    # In particular, a dishwasher water-tank comparison must not become a
    # dehumidifier or an air-conditioner drain-hose photograph.
    if '식기세척기' in source:
        return ('Show an unmistakable compact kitchen dishwasher with its open '
                'dish rack and plates. For water-tank supply, show the dishwasher '
                'reservoir being filled; for direct supply, show the dishwasher '
                'connected to a kitchen faucet. Never show an air conditioner, '
                'outdoor condenser, dehumidifier, purifier or laundry washer.')
    if '건조기' in source and index == 1:
        return (
            'Show one recognizable front-loading tumble clothes dryer with its circular door. '
            'Make the actual lint-filter seat or lower filter opening clearly visible in the frame, '
            'with the removable filter visibly fitted into that opening or pulled partway out from it. '
            'Keep the filter seat attached to the dryer and large enough to identify at a glance; '
            'do not substitute loose lint, a filter lying on a counter, or a generic grille. '
            'Never show an air purifier, dehumidifier or unrelated appliance.'
        )
    if '건조기' in source and index == 3:
        return (
            'Create a like-for-like comparison using the same front-loading tumble clothes dryer '
            'in both halves, at the same camera height, distance, lens, scale, orientation and ordinary light. '
            'Keep the same circular door and lower lint-filter opening in the same position in both halves. '
            'LEFT: the removable lint filter is correctly seated in its opening. '
            'RIGHT: that same filter is pulled partway out of the same opening so its lint surface is visible. '
            'The dryer and filter seat must remain equally clear and large on both sides. '
            'Do not compare a filter with the dryer side, a laundry basket, loose lint, or a different appliance. '
            'Never show an air purifier or dehumidifier.'
        )
    locks = (
        (('노트북', 'RAM', '램', '발열'), 'Show a real open laptop on a clean desk with its ventilation path, cooling fan area, bottom intake grille, memory module or system monitoring context as required by the card. When RAM is mentioned, show a realistic laptop memory module and slot only if the action calls for it. Never show source code, terminal windows, Codex, ChatGPT, AI logos, abstract app UI, floating icons or generic technology cubes.'),
        (('고무패킹', '문틈', '곰팡이'), (
            'ONLY photograph a household REFRIGERATOR door gasket: the flexible folded '
            'rubber seal attached around the rectangular refrigerator door edge. '
            'It must not have a circular glass drum door. NEVER show a washing machine, '
            'laundry room, washer controls, document, ruler, generic appliance icon or '
            'unrelated filter. '
            + {
                1: 'Show one close crop of the gasket visibly lifting away from the refrigerator frame; keep the problem unmistakable.',
                2: 'Show an extreme documentary close-up of moisture and small dust trapped inside the folded gasket groove. Do not show a whole appliance.',
                3: 'Show the OPEN RECTANGULAR REFRIGERATOR cabinet and its tall vertical door edge in both halves; visible interior shelves, bottles and food containers must prove this is a refrigerator. Fill the lower scene with a same-scale side-by-side physical comparison: LEFT gasket lies flat and evenly sealed on the refrigerator; RIGHT gasket is visibly lifted or split on the refrigerator. Both halves must be unmistakably different. Absolutely no circular door, round gasket, glass porthole, drum or laundry appliance anywhere. Do not leave a blank lower area.',
                4: 'Show one natural hand gently opening the gasket groove to inspect moisture, discoloration and a small split. The rectangular refrigerator door edge must remain visible.',
                5: 'Show a dry cloth beside a cleaned gasket that still lifts slightly from the rectangular refrigerator frame, making the need for inspection clear.',
            }.get(index, 'Show the exact gasket condition and action named by the card.')
        )),
        (('배수호스', '실외기'), 'Show the exact home air-conditioner inspection subject named in the card: a wall-mounted indoor air conditioner connected to a real flexible drain hose, or an outdoor condenser unit with open airflow and no cover. When the card is about the drain hose, make the hose, bend, outlet and water path the main subject. Never replace them with a ruler, document, generic appliance or abstract icon.'),
        (('제습기', '물통'), 'Show a recognizable floor-standing home dehumidifier with its removable transparent water tank pulled out, remaining water droplets, the tank lid and a clean cloth or drying rack. Make emptying and fully air-drying the tank visually obvious. Never substitute an air purifier, humidifier, refrigerator, document or generic white appliance.'),
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
    # Every production call loads the canonical v3 files. Missing or weakened
    # contracts fail closed instead of silently falling back to an older prompt.
    canonical_prompt, production_contract = load_production_style_bundle()
    ai_gate = (production_contract.get('qa') or {}).get('ai_likeness_max_exclusive')
    human_signals = '; '.join(production_contract.get('human_edit_signals') or [])
    forbidden = '; '.join(production_contract.get('forbidden') or [])
    # The cover explorer owns technology/UI motifs. Production backgrounds use
    # only the factual card role and real-life subject; typography is added later.
    role_key = ROLE_KEYS[index - 1]
    parts = [
        'Create one square full-bleed documentary photograph for a Korean home-appliance help article. This generation is the photograph layer only, never a finished card design.',
        f'Card role: {ROLES[index - 1]}. Topic: {card.get("headline", "")}.',
        f'Explanation: {card.get("copy", "")}. Visible situation and objects: {item_text}.',
        _subject_lock(card, index),
        'Show a believable, modest, actually used Korean home interior and concrete relevant appliances and actions. Preserve tiny signs of ordinary life: slight surface wear, faint fingerprints, an imperfectly folded cloth, small natural dust or water traces, mildly uneven spacing and one or two relevant background objects. Keep them subtle and physically plausible, never dirty for effect. Use ordinary window light or ceiling light with realistic falloff, not studio lighting. Do not use documents, packaging, certificates, reports, phone screens, control-panel text or labels as visual evidence; all readable information will be typeset locally later.',
        'The image must explain the situation visually, with a clear subject and natural scale.',
        f'Production card role: {role_key}. {ROLE_SCENE_DIRECTIONS[index - 1]}',
        'Generate a plain real-life photograph only. Do not design a card, layout, poster, checklist, comparison board or infographic inside the photograph. '
        'Do not place paper notes, printed cards, colored panels, frames, captions or readable marks anywhere in the scene. '
        'All pastel surfaces, typography and editorial layout are added later by deterministic local code. '
        'Do not copy cover-exploration motifs, software screens, code, terminals, chat windows or AI branding.',
        'Use neutral daylight with accurate whites and cool natural shadows. No beige, yellow cream, amber light or warm sepia cast.',
        'The concrete scene and objects must occupy roughly 70 to 82 percent of the frame and remain the first thing seen. '
        'Reserve low-detail space only where this card role explicitly requests it; keep every other area visually complete. '
        'Never create a large empty lower band or an accidental blank half.',
        'No people unless hands are essential to demonstrate the action.',
        'ABSOLUTELY NO text, letters, numbers, logos, labels, UI glyphs, watermark, yellow cast, beige cream, sepia, collage, source code, terminal, Codex, ChatGPT, AI branding, floating icons, generic infographic nodes, repeated template boxes, circles, arrows, badges, check marks, crosses, decorative labels, decorative waves, curved swooshes, ornamental underlines, glossy product-ad lighting, perfect showroom cleanliness, cinematic bokeh or synthetic depth of field.',
        f'Canonical v3 human-edit signals: {human_signals}.',
        f'Canonical v3 forbidden signals: {forbidden}.',
        _subject_lock(card, index),
        'FINAL CHECK: output only the requested real photograph, with zero text, zero printed material and zero graphic-design layers.',
        f'This scene must be capable of passing the strict AI-likeness gate below {ai_gate}/100 after local Korean typesetting.',
        'Apply the following canonical production prompt as binding art direction. '
        'Where it discusses typography, reserve space only; never draw text inside the generated scene:\n'
        + canonical_prompt,
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
        # Five intentionally different editorial structures. Typography is local and exact,
        # but no repeated bottom panel, badge row, callout circle, arrow or decorative icon is used.
        panel_rgb = tuple(int(style['surface'][i:i + 2], 16) for i in (1, 3, 5))
        accent_rgb = tuple(int(accent[i:i + 2], 16) for i in (1, 3, 5))
        layouts = {
            1: {'panel': (42, 510, 770, 1040), 'role': (70, 534), 'number': (640, 534),
                'title': (70, 584, 730, 682), 'copy': (70, 700, 730, 770),
                'items': (70, 800, 730, 1020), 'align': 'left'},
            2: {'panel': (36, 82, 462, 998), 'role': (68, 108), 'number': (366, 108),
                'title': (68, 168, 430, 302), 'copy': (68, 326, 430, 426),
                'items': (68, 468, 430, 962), 'align': 'left'},
            3: {'panel': (72, 42, 1008, 490), 'role': (104, 65), 'number': (866, 65),
                'title': (104, 116, 976, 205), 'copy': (104, 218, 976, 275),
                'items': (104, 310, 976, 470), 'align': 'center'},
            4: {'panel': (532, 102, 1042, 1008), 'role': (562, 127), 'number': (910, 127),
                'title': (562, 182, 1002, 304), 'copy': (562, 322, 1002, 430),
                'items': (562, 464, 1002, 974), 'align': 'left'},
            5: {'panel': (350, 530, 1038, 1038), 'role': (380, 555), 'number': (906, 555),
                'title': (380, 608, 1000, 702), 'copy': (380, 714, 1000, 775),
                'items': (380, 800, 1000, 1010), 'align': 'right'},
        }
        spec = layouts[index]
        x1, y1, x2, y2 = spec['panel']
        # Role-specific surfaces prevent a mechanically repeated white-card template.
        # These are flat cool tints, never gradients or decorative devices.
        role_surfaces = {
            1: (247, 248, 252, 255),  # compact cool-white note
            2: (232, 228, 247, 255),  # lavender vertical field
            3: (224, 239, 246, 255),  # powder-blue top band
            4: (222, 241, 235, 255),  # mint right column
            5: (247, 230, 232, 255),  # restrained coral decision block
        }
        if index in {1, 5}:
            vd.rounded_rectangle((x1, y1, x2, y2), radius=18, fill=role_surfaces[index])
        elif index == 3:
            vd.rectangle((0, y1, 1080, y2), fill=role_surfaces[index])
        else:
            vd.rectangle((x1, y1, x2, y2), fill=role_surfaces[index])
        # Leave the scene unobscured outside the text area. No repeated accent
        # rule, wave, badge, icon or ornamental underline is added.
        canvas = Image.alpha_composite(canvas, veil)
        draw = ImageDraw.Draw(canvas)

        headline = str(card.get('headline') or '').strip()
        copy = str(card.get('copy') or '').strip()
        # Omit the redundant role label. It was read as clipped helper copy in visual QA
        # and added a template-like accent without carrying article information.
        title_font = _fit(draw, headline, font_path, 54, spec['title'], minimum=32)
        copy_font = _fit(draw, copy, font_path, 27, spec['copy'], minimum=20)
        anchor = 'ma' if spec['align'] == 'center' else ('ra' if spec['align'] == 'right' else None)
        title_x = (spec['title'][0] + spec['title'][2]) // 2 if anchor == 'ma' else (spec['title'][2] if anchor == 'ra' else spec['title'][0])
        copy_x = (spec['copy'][0] + spec['copy'][2]) // 2 if anchor == 'ma' else (spec['copy'][2] if anchor == 'ra' else spec['copy'][0])
        draw.multiline_text((title_x, spec['title'][1]), headline, font=title_font, fill='#202329', spacing=8, anchor=anchor)
        draw.multiline_text((copy_x, spec['copy'][1]), copy, font=copy_font, fill='#343741', spacing=7, anchor=anchor)

        # Every item contains distinct information from the approved five-card
        # plan. The former renderer dropped label/detail entirely, leaving an
        # empty white panel and making comparison/checklist cards unusable.
        item_box = spec['items']
        items = card.get('items') or []
        if not items or any(not isinstance(item, dict) or
                            not str(item.get('label') or '').strip() or
                            not str(item.get('detail') or '').strip() for item in items):
            raise ValueError(f'Blog card {index} has incomplete items')
        slot_height = (item_box[3] - item_box[1]) // len(items)
        for item_index, item in enumerate(items):
            if index == 3:
                # Comparison items map left-to-right to the two photographed conditions.
                gap = 28
                column_width = (item_box[2] - item_box[0] - gap) // 2
                item_left = item_box[0] + item_index * (column_width + gap)
                item_right = item_left + column_width
                top = item_box[1]
                current_slot_height = item_box[3] - item_box[1]
            else:
                item_left, item_right = item_box[0], item_box[2]
                top = item_box[1] + item_index * slot_height
                current_slot_height = slot_height
            label = str(item['label']).strip()
            detail = str(item['detail']).strip()
            label_box = (item_left, top, item_right,
                         top + min(38, current_slot_height // 2))
            detail_box = (item_left, top + min(38, current_slot_height // 2),
                          item_right, top + current_slot_height - 4)
            label_font = _fit(draw, label, font_path, 24, label_box, minimum=16)
            detail_font = None
            wrapped = ''
            for font_size in range(19, 12, -1):
                candidate_font = _font(font_path, font_size)
                candidate = _wrap_to_width(
                    draw, detail, candidate_font, detail_box[2] - detail_box[0]
                )
                bounds = draw.multiline_textbbox((0, 0), candidate,
                                                  font=candidate_font, spacing=3)
                if bounds[3] - bounds[1] <= detail_box[3] - detail_box[1]:
                    detail_font, wrapped = candidate_font, candidate
                    break
            if detail_font is None:
                raise ValueError(f'Blog card {index} item {item_index + 1} does not fit')
            item_anchor = (
                'ma' if index == 3 else ('ra' if spec['align'] == 'right' else None)
            )
            item_x = (
                (item_left + item_right) // 2 if item_anchor == 'ma'
                else (item_right if item_anchor == 'ra' else item_left)
            )
            draw.text((item_x, top), label, font=label_font,
                      fill='#202329', anchor=item_anchor)
            draw.multiline_text((item_x, detail_box[1]), wrapped,
                                font=detail_font, fill='#343741',
                                spacing=3, anchor=item_anchor)

        canvas.convert('RGB').save(final_path, quality=95)
        results.append(final_path)
    return results
