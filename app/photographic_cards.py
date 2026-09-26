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


def _font(path: str | None, size: int, weight: int = 450) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [path, '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc',
                  '/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf']
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            font = ImageFont.truetype(candidate, size=size)
            try:
                axes = font.get_variation_axes()
                if axes:
                    font.set_variation_by_axes([weight] + [axis['default'] for axis in axes[1:]])
            except (AttributeError, OSError, ValueError):
                pass
            return font
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


def _fit_wrapped(
    draw: ImageDraw.ImageDraw, text: str, path: str | None, maximum: int,
    box: tuple[int, int, int, int], *, minimum: int = 18, weight: int = 420,
) -> tuple[ImageFont.ImageFont, str]:
    """Fit supporting copy by wrapping at word/character boundaries, never clipping."""
    x1, y1, x2, y2 = box
    for size in range(maximum, minimum - 1, -1):
        font = _font(path, size, weight)
        wrapped = _wrap_to_width(draw, text, font, x2 - x1)
        left, top, right, bottom = draw.multiline_textbbox(
            (0, 0), wrapped, font=font, spacing=7,
        )
        if right - left <= x2 - x1 and bottom - top <= y2 - y1:
            return font, wrapped
    raise ValueError('Korean overlay copy does not fit its reserved region')


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


def _wrap_title(draw: ImageDraw.ImageDraw, value: str,
                font: ImageFont.ImageFont, width: int) -> str:
    """Preserve authored breaks; otherwise split at a balanced word boundary."""
    if '\n' in value:
        return value
    if draw.textbbox((0, 0), value, font=font)[2] <= width:
        return value
    words = value.split()
    candidates = []
    for split in range(1, len(words)):
        lines = (' '.join(words[:split]), ' '.join(words[split:]))
        widths = [draw.textbbox((0, 0), line, font=font)[2] for line in lines]
        if max(widths) <= width:
            candidates.append(((max(widths), abs(widths[0] - widths[1])), lines))
    if candidates:
        return '\n'.join(min(candidates, key=lambda item: item[0])[1])
    return _wrap_to_width(draw, value, font, width)


def _fit_title(draw: ImageDraw.ImageDraw, value: str, path: str | None,
               box: tuple[int, int, int, int]) -> tuple[ImageFont.ImageFont, str]:
    x1, y1, x2, y2 = box
    for size in range(60, 57, -1):
        font = _font(path, size, 700)
        wrapped = _wrap_title(draw, value, font, x2 - x1)
        bounds = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=5)
        if bounds[2] - bounds[0] <= x2 - x1 and bounds[3] - bounds[1] <= y2 - y1:
            return font, wrapped
    raise ValueError('Korean card title does not fit the v7 title region at 58-60px')


def _paste_scene(canvas: Image.Image, background: Image.Image,
                 box: tuple[int, int, int, int]) -> None:
    x1, y1, x2, y2 = box
    scene = ImageOps.fit(background, (x2 - x1, y2 - y1), method=Image.Resampling.LANCZOS)
    canvas.paste(scene, (x1, y1))


def _draw_v7_text(canvas: Image.Image, background: Image.Image,
                   card: dict[str, Any], index: int, title_font_path: str | None,
                   body_font_path: str | None) -> None:
    """Apply the v7 five-role editorial grid to a generated photo layer."""
    colors = {
        'white': '#FFFFFF', 'paper': '#F5F6F8', 'ink': '#171A22',
        'muted': '#646C78', 'rule': '#C8CED8', 'blue': '#3158D8',
        'blue_pale': '#EAF0FF', 'lilac': '#C9B8E8', 'mint': '#A7DDCE',
        'soft': '#F0F2F5',
    }
    draw = ImageDraw.Draw(canvas)
    text_path = body_font_path or title_font_path
    title = str(card.get('headline') or '').strip()
    copy = str(card.get('copy') or '').strip()
    items = card.get('items') or []
    if not title or not copy or not items or any(
        not isinstance(item, dict) or not str(item.get('label') or '').strip()
        or not str(item.get('detail') or '').strip() for item in items
    ):
        raise ValueError(f'Blog card {index} has incomplete v7 copy')

    # Role-specific image and text rectangles follow the confirmed v7 hierarchy.
    if index == 1:
        canvas.paste(colors['white'], (0, 0, 1080, 1080))
        spec = {'title': (68, 132, 1010, 298), 'copy': (72, 310, 1008, 360),
                'scene': (62, 398, 1018, 770), 'items': (64, 812, 1016, 1000),
                'surface': colors['white'], 'title_align': 'left'}
    elif index == 2:
        canvas.paste(colors['white'], (0, 0, 1080, 1080))
        draw.rectangle((0, 0, 1080, 295), fill=colors['blue_pale'])
        spec = {'title': (64, 92, 1010, 245), 'copy': (64, 250, 1010, 292),
                'scene': (48, 342, 494, 906), 'items': (548, 354, 1016, 900),
                'surface': colors['soft'], 'title_align': 'left'}
    elif index == 3:
        canvas.paste(colors['paper'], (0, 0, 1080, 1080))
        spec = {'title': (64, 98, 1010, 254), 'copy': (64, 258, 1016, 302),
                'scene': (64, 326, 1016, 610), 'items': (64, 646, 1016, 842),
                'surface': colors['paper'], 'title_align': 'left'}
    elif index == 4:
        canvas.paste(colors['white'], (0, 0, 1080, 1080))
        spec = {'title': (64, 112, 654, 292), 'copy': (64, 312, 654, 438),
                'scene': (698, 76, 1016, 458), 'items': (64, 540, 1016, 938),
                'surface': colors['mint'], 'title_align': 'left'}
    else:
        canvas.paste(colors['white'], (0, 0, 1080, 1080))
        spec = {'title': (64, 98, 1010, 250), 'copy': (68, 270, 1012, 326),
                'scene': (48, 350, 1032, 600), 'items': (68, 648, 1012, 1010),
                'surface': colors['blue_pale'], 'title_align': 'left'}

    _paste_scene(canvas, background, spec['scene'])
    draw = ImageDraw.Draw(canvas)
    # Place one quiet folio in the shared top-right corner, as in v7.
    number_font = _font(text_path, 22, 500)
    draw.text((1012, 48), f'{index:02d}', font=number_font,
              fill=colors['blue'], anchor='ra')

    title_box = spec['title']
    title_font, title_render = _fit_title(draw, title, title_font_path, title_box)
    draw.multiline_text((title_box[0], title_box[1]), title_render,
                        font=title_font, fill=colors['ink'], spacing=5)

    copy_box = spec['copy']
    copy_max = 32 if index != 5 else 30
    copy_font, copy_render = _fit_wrapped(
        draw, copy, text_path, copy_max, copy_box, minimum=27, weight=420,
    )
    draw.multiline_text((copy_box[0], copy_box[1]), copy_render,
                        font=copy_font, fill=colors['muted'], spacing=8)

    item_box = spec['items']
    if index in {1, 2, 5}:
        columns = 1 if index == 2 else len(items)
        rows = len(items) if columns == 1 else 1
        gap = 24 if columns > 1 else 0
        cell_w = (item_box[2] - item_box[0] - gap * (columns - 1)) // columns
        cell_h = (item_box[3] - item_box[1]) // rows
        positions = [
            (item_box[0] + (i % columns) * (cell_w + gap),
             item_box[1] + (i // columns) * cell_h, cell_w, cell_h)
            for i in range(len(items))
        ]
    elif index == 3:
        gap = 28
        cell_w = (item_box[2] - item_box[0] - gap) // 2
        positions = [(item_box[0] + i * (cell_w + gap), item_box[1], cell_w,
                      item_box[3] - item_box[1]) for i in range(len(items))]
    else:
        columns = 2
        gap_x, gap_y = 48, 28
        cell_w = (item_box[2] - item_box[0] - gap_x) // 2
        cell_h = (item_box[3] - item_box[1] - gap_y) // 2
        positions = [
            (item_box[0] + (i % columns) * (cell_w + gap_x),
             item_box[1] + (i // columns) * (cell_h + gap_y), cell_w, cell_h)
            for i in range(len(items))
        ]

    for item_index, (item, (x, y, width, height)) in enumerate(zip(items, positions)):
        label = str(item['label']).strip()
        detail = str(item['detail']).strip()
        label_font, label_render = _fit_wrapped(
            draw, label, text_path, 30 if index not in {4, 5} else 28,
            (x, y, x + width, y + min(58, height // 2)), minimum=26, weight=650,
        )
        label_bounds = draw.multiline_textbbox((0, 0), label_render, font=label_font, spacing=3)
        label_height = label_bounds[3] - label_bounds[1]
        detail_top = y + label_height + 14
        detail_font, detail_render = _fit_wrapped(
            draw, detail, text_path, 28, (x, detail_top, x + width, y + height), minimum=25, weight=420,
        )
        draw.multiline_text((x, y), label_render, font=label_font,
                            fill=colors['ink'], spacing=3)
        draw.multiline_text((x, detail_top), detail_render, font=detail_font,
                            fill=colors['muted'], spacing=6)
        if index == 2 and item_index < len(items) - 1:
            draw.line((x, y + height - 10, x + width, y + height - 10),
                      fill=colors['rule'], width=1)


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
    if '비데' in source and index == 4:
        return ('Use a close documentary crop of a bidet supply-hose connection after reinstallation, '
                'with a hand holding a dry tissue near (not opening or turning) the connection to look for moisture. '
                'Show ordinary bathroom tile and a small used towel for lived-in context; do not show a full product hero shot. '
                'Do not imply that users should manipulate a valve unless their model manual explicitly says so.')
    if '비데' in source and index == 5:
        return ('Show an ordinary moving-day bathroom scene from a wider doorway view: a partially packed moving box '
                'with a small bag of bidet mounting parts in the foreground and a toilet with a bidet seat farther back. '
                'Include modest lived-in details and natural uneven daylight. Keep the bidet secondary, not a glossy product close-up. '
                'No readable text, labels, logos, staged product advertising, or repeated close-up composition.')
    if '비데' in source:
        return ('Show a clearly recognizable non-branded electronic bidet seat attached to a toilet, '
                'with its bidet water hose and accessible shutoff/diverter valve in a bathroom. '
                'Never show a washing machine, laundry hose, kitchen appliance, or unrelated water connection.')
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
    body_font_path: str | None = None,
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
        canvas = Image.new('RGB', (1080, 1080), '#FFFFFF')
        _draw_v7_text(canvas, background, card, index, font_path, body_font_path)
        canvas.save(final_path, quality=95)
        results.append(final_path)
    return results
