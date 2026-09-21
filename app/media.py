from __future__ import annotations
import base64
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
from openai import OpenAI
import httpx

from .budget import BudgetGuard
from .card_design import get_design_blueprint, get_design_language

PALETTE = [
    '#F7F8FB', '#E7F1FF', '#EDE9FE', '#FFE8EF', '#DFF7EE',
    '#E8F5F7', '#F0EAF8', '#F8EAF2', '#E6F2EC', '#E9EEF7',
    '#F3E8EE', '#E4F0F2', '#ECE8F5', '#F7E9E6', '#E2EFE7',
    '#E8ECF4', '#F1E7F0', '#E5F1EE', '#EEEAF3', '#E7EDF2',
    '#F5E8EC', '#E3EFF5', '#EBE7F2', '#E6F3F0',
]
TEXT = '#20242C'
ACCENT = '#5B67D8'
BLOG_SYMBOLS = {
    'bubbles', 'laundry', 'wifi', 'document', 'appliance', 'measurement',
    'home', 'diagram', 'garment', 'steamer', 'iron', 'care_label', 'wrinkle',
}

SHORTS_GUIDE_VERSION = '2026-09-17-master-video-v1'
PPOJJUGI_REFERENCE_LAYOUT = 'blurred-background/white-horizontal-panel/top-brand/scene-number/bottom-caption'
JAPAN_REFERENCE_LAYOUT = 'full-frame-illustration/first-scene-top-hook/bottom-japanese-caption'


class MediaGenerator:
    def __init__(
        self,
        api_key: str,
        image_model: str,
        tts_model: str,
        voice: str,
        font_path: str | None = None,
        image_quality: str = 'medium',
        budget: BudgetGuard | None = None,
        image_estimated_cost_usd: float = 0.05,
        tts_estimated_cost_usd: float = 0.03,
        fal_key: str = '',
        tts_language_code: str | None = None,
        tts_temperature: float = 1.1,
    ):
        self.client = OpenAI(api_key=api_key)
        self.image_model = image_model
        self.tts_model = tts_model
        self.voice = voice
        self.font_path = font_path
        self.image_quality = image_quality
        self.budget = budget
        self.image_estimated_cost_usd = image_estimated_cost_usd
        self.tts_estimated_cost_usd = tts_estimated_cost_usd
        self.fal_key = fal_key
        self.tts_language_code = tts_language_code
        self.tts_temperature = tts_temperature

    def generate_scene_images(
        self,
        scenes: list[dict[str, Any]],
        out_dir: Path,
        channel_style: str = 'japan_shorts',
    ) -> list[Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        if channel_style == 'ppojjugi_shorts':
            image_size = '1536x1024'
            style_suffix = (
                'Landscape 3:2 hand-drawn 2D animation still, full bleed with no border, no inset frame, and no blank margins. '
                'Match the supplied hamster exactly: golden-orange fur, cream muzzle and belly, tiny black bean eyes, pink nose, two front teeth, lavender shirt, dark green apron. '
                'Use an intimate medium close-up or close-up; the hamster must occupy 35 to 60 percent of the frame and its face must be clearly readable. '
                'Thin slightly imperfect pencil line, painterly muted colors, subtle soft shading, detailed urban or domestic environment, quiet weary emotion. '
                'Use restrained dusk or indoor cinematic values with clear midtone contrast; never high-key white, washed-out, empty, tiny-subject, or nursery-pastel composition. '
                'No text, no photorealism, no 3D, no glossy advertising look, no yellow cast, no sepia.'
            )
        elif channel_style == 'japan_shorts':
            image_size = '1024x1536'
            style_suffix = (
                'Vertical 2:3 Japanese hand-drawn watercolor and colored-pencil story illustration. '
                'Visible soft paper grain, delicate pencil outlines, restrained realistic proportions, quiet Showa-era atmosphere. '
                'Not a photograph and not photorealistic; no camera, lens, cinematic photo, or glossy advertising style. '
                'Cool white natural light with muted gray-blue, faded green, lavender, and soft brown. '
                'No text, no yellow cast, no mustard, no gold filter, no sepia.'
            )
        else:
            raise ValueError(f'Unknown Shorts image style: {channel_style}')
        reference_files = {
            'ppojjugi_shorts': Path('assets/reference/ppijuk_style_board.jpg.b64'),
            'japan_shorts': Path('assets/reference/japan_style_board.jpg.b64'),
        }
        reference_b64 = reference_files[channel_style]
        if not reference_b64.exists():
            raise RuntimeError(f'Missing required style reference: {reference_b64}')
        reference_path = out_dir / '_style_reference.jpg'
        reference_path.write_bytes(base64.b64decode(reference_b64.read_text(encoding='utf-8').strip()))
        style_suffix += (
            ' Use the supplied reference board only for its exact illustration language, line quality, '
            'color treatment, composition density, character proportions, caption-safe layout, and emotional restraint. '
            'Create one new coherent scene, not a collage or contact sheet. Do not copy the reference story or any text.'
        )
        for i, scene in enumerate(scenes, 1):
            prompt = str(scene.get('image_prompt') or scene.get('caption') or '')
            if self.budget:
                self.budget.reserve(
                    'image_generation',
                    self.image_estimated_cost_usd,
                    {'model': self.image_model, 'quality': self.image_quality, 'scene': i},
                )
            with reference_path.open('rb') as reference_image:
                response = self.client.images.edit(
                    model=self.image_model,
                    image=reference_image,
                    prompt=prompt + '\n' + style_suffix,
                    size=image_size,
                    quality=self.image_quality,
                )
            item = response.data[0]
            b64 = getattr(item, 'b64_json', None)
            if not b64:
                raise RuntimeError('Image API returned no base64 image data')
            path = out_dir / f'scene_{i:02d}.png'
            path.write_bytes(base64.b64decode(b64))
            paths.append(path)
        return paths

    def generate_tts(
        self,
        text: str,
        out_path: Path,
        estimated_cost_usd: float | None = None,
        instructions: str = '',
    ) -> Path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if self.budget:
            self.budget.reserve(
                'tts_generation',
                self.tts_estimated_cost_usd if estimated_cost_usd is None else estimated_cost_usd,
                {'model': self.tts_model, 'characters': len(text)},
            )
        if self.tts_model.startswith('fal-ai/'):
            # Never mix providers inside one Short. The former OpenAI fallback
            # changed the speaker mid-video and produced the artificial voice
            # shifts heard in review. Retry the approved Fal voice and fail
            # closed if it remains unavailable.
            last_error: Exception | None = None
            for attempt in range(3):
                try:
                    self._generate_fal_tts(text, out_path, instructions)
                    last_error = None
                    break
                except (httpx.HTTPStatusError, TimeoutError) as exc:
                    last_error = exc
                    if isinstance(exc, httpx.HTTPStatusError):
                        status = exc.response.status_code
                        if status not in {408, 422, 429, 500, 502, 503, 504}:
                            raise
                    if attempt < 2:
                        time.sleep(2 ** attempt)
            if last_error is not None:
                raise RuntimeError(
                    'Approved Fal voice failed after three attempts; stopped '
                    'instead of changing the speaker or provider'
                ) from last_error
        else:
            with self.client.audio.speech.with_streaming_response.create(
                model=self.tts_model,
                voice=self.voice,
                input=text,
                instructions=instructions,
            ) as response:
                response.stream_to_file(out_path)
        return out_path

    def _generate_fal_tts(self, text: str, out_path: Path, instructions: str) -> None:
        if not self.fal_key:
            raise RuntimeError('FAL_KEY is required for the approved Shorts TTS endpoint')
        output_format = out_path.suffix.lower().lstrip('.')
        if output_format not in {'wav', 'mp3', 'ogg_opus'}:
            output_format = 'mp3'
        headers = {'Authorization': f'Key {self.fal_key}', 'Content-Type': 'application/json'}
        payload = {
            'prompt': text,
            'style_instructions': instructions,
            'voice': self.voice,
            'language_code': self.tts_language_code,
            'temperature': self.tts_temperature,
            'output_format': output_format,
        }
        submit_url = f'https://queue.fal.run/{self.tts_model}'
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            try:
                submitted = client.post(submit_url, headers=headers, json=payload)
                submitted.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in {401, 402, 403}:
                    raise RuntimeError(
                        'Fal TTS authorization or billing identity failed. Verify the connected Fal account, '
                        'FAL_KEY account, and billing account match before treating this as insufficient credit.'
                    ) from exc
                raise
            job = submitted.json()
            status_url = job['status_url']
            response_url = job['response_url']
            deadline = time.monotonic() + 240
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f'Fal TTS request {job.get("request_id", "unknown")} timed out; do not resubmit '
                        'until this request ID is checked.'
                    )
                status = client.get(status_url, headers=headers)
                status.raise_for_status()
                state = status.json().get('status')
                if state == 'COMPLETED':
                    break
                if state not in {'IN_QUEUE', 'IN_PROGRESS'}:
                    raise RuntimeError(f'Unexpected Fal TTS queue state: {state!r}')
                time.sleep(1.0)
            result_response = client.get(response_url, headers=headers)
            result_response.raise_for_status()
            audio_url = result_response.json()['audio']['url']
            audio_response = client.get(audio_url)
            audio_response.raise_for_status()
            out_path.write_bytes(audio_response.content)


def probe_media_duration(path: Path) -> float:
    if not shutil.which('ffprobe'):
        raise RuntimeError('ffprobe is required for narration timing')
    result = subprocess.run([
        'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1', str(path),
    ], check=True, capture_output=True, text=True)
    duration = float(result.stdout.strip())
    if duration <= 0:
        raise RuntimeError(f'Invalid media duration: {path}')
    return duration


def concat_scene_audio(parts: list[Path], out_path: Path) -> tuple[Path, list[float]]:
    if not parts:
        raise ValueError('At least one scene narration file is required')
    if not shutil.which('ffmpeg'):
        raise RuntimeError('ffmpeg is required for narration timing')
    out_path.parent.mkdir(parents=True, exist_ok=True)
    concat_file = out_path.parent / 'narration_concat.txt'
    concat_file.write_text(''.join(f"file '{part.resolve().as_posix()}'\n" for part in parts), encoding='utf-8')
    subprocess.run([
        'ffmpeg', '-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0',
        '-i', str(concat_file), '-c:a', 'libmp3lame', '-b:a', '192k', str(out_path),
    ], check=True)
    return out_path, [probe_media_duration(part) for part in parts]


def render_blog_cards(
    cards: list[dict[str, Any]], out_dir: Path, font_path: str | None = None,
    *, card_format: str = 'square', visual_family: str = 'playful_diagram',
    design_language: str | None = None, design_blueprint: dict[str, Any] | None = None,
) -> list[Path]:
    """Typeset five original explanatory diagrams; never invent product photos.

    All text is measured before files are saved. A missing structure or overflow
    is a production failure, not permission to drop content or shrink it away.
    """
    layouts = ('cover', 'flow', 'comparison', 'checklist', 'decision')
    if len(cards) != 5:
        raise ValueError('Blog card set must contain exactly five cards')
    formats = {'square': (1080, 1080), 'landscape_4_3': (1448, 1086)}
    if card_format not in formats:
        raise ValueError('Blog card format must be square or landscape_4_3')
    family_palettes = {
        'soft_scene': ('#DCD8F4', '#F4CBC7', '#DCE8F5', '#303047'),
        'playful_diagram': ('#C9B8E8', '#A7DDCE', '#F7A6A6', '#202329'),
        'contract_notebook': ('#D8B7E5', '#D9E3A6', '#B9D7F3', '#302735'),
        'clipboard_checklist': ('#B9D3A8', '#B9D7F3', '#E7D2B0', '#173C2B'),
    }
    if visual_family not in family_palettes:
        raise ValueError('Unsupported Hamzzu blog visual family')
    named_style = get_design_language(design_language) if design_language is not None else None
    canonical_blueprint = get_design_blueprint(design_language) if design_language is not None else None
    if design_blueprint is not None and design_blueprint != canonical_blueprint:
        raise ValueError('Named card-news design blueprint does not match the selected language')
    width, height = formats[card_format]
    sx, sy = width / 1080, height / 1080
    sf = min(sx, sy)

    def box(values):
        return tuple(int(v * (sx if i % 2 == 0 else sy)) for i, v in enumerate(values))

    def point(values):
        return int(values[0] * sx), int(values[1] * sy)

    def size(value):
        return max(1, int(value * sf))

    rendered: list[Image.Image] = []
    palette = named_style['palette'] if named_style else family_palettes[visual_family]
    primary, secondary, tertiary, ink = palette
    background = named_style['background'] if named_style else '#FFFFFF'
    surface = named_style['surface'] if named_style else '#FFFFFF'
    accent_text = named_style['accent_text'] if named_style else ink
    title_size = named_style['title_size'] if named_style else 64
    copy_size = named_style['copy_size'] if named_style else 33
    for i, card in enumerate(cards, 1):
        layout = card.get('layout')
        if layout != layouts[i - 1] or card.get('card') != i:
            raise ValueError(f'Card {i} must use layout={layouts[i - 1]} and matching card number')
        headline = _blog_required_text(card.get('headline'), f'card {i} headline')
        copy = _blog_required_text(card.get('copy'), f'card {i} copy')
        illustration = card.get('illustration')
        # A model may occasionally repeat a visual-family token in the
        # illustration slot. All four mappings are deliberately generic and
        # add no product shape or factual detail, so a known schema slip can be
        # recovered without weakening the fail-closed rule for unknown tokens.
        illustration = {
            'soft_scene': 'home',
            'playful_diagram': 'diagram',
            'contract_notebook': 'document',
            'clipboard_checklist': 'document',
            'filter': 'appliance',
            'air_filter': 'appliance',
            'air_purifier': 'appliance',
            'airflow': 'diagram',
            'dishwasher': 'appliance',
            'dishwasher_filter': 'appliance',
            'rinse_aid': 'bubbles',
            'water_flow': 'diagram',
            'drain': 'diagram',
            'spray_nozzle': 'appliance',
            'glassware': 'home',
        }.get(illustration, illustration)
        if illustration not in BLOG_SYMBOLS:
            raise ValueError(f'Card {i} has an unsupported explanatory illustration')
        items = card.get('items')
        minimum, maximum = {'cover': (2, 2), 'flow': (3, 3), 'comparison': (2, 2),
                            'checklist': (3, 4), 'decision': (3, 3)}[layout]
        if not isinstance(items, list) or not minimum <= len(items) <= maximum:
            raise ValueError(f'Card {i} {layout} requires {minimum}..{maximum} structured items')
        for item in items:
            if not isinstance(item, dict):
                raise ValueError(f'Card {i} item must contain label and detail')
            _blog_required_text(item.get('label'), f'card {i} item label')
            _blog_required_text(item.get('detail'), f'card {i} item detail')
            item_illustration = {
                'soft_scene': 'home',
                'playful_diagram': 'diagram',
                'contract_notebook': 'document',
                'clipboard_checklist': 'document',
                'filter': 'appliance',
                'air_filter': 'appliance',
                'air_purifier': 'appliance',
                'airflow': 'diagram',
                'dishwasher': 'appliance',
                'dishwasher_filter': 'appliance',
                'rinse_aid': 'bubbles',
                'water_flow': 'diagram',
                'drain': 'diagram',
                'spray_nozzle': 'appliance',
                'glassware': 'home',
            }.get(item.get('illustration'), item.get('illustration'))
            if item_illustration is not None and item_illustration not in BLOG_SYMBOLS:
                raise ValueError(f'Card {i} item has an unsupported explanatory illustration')
            if item_illustration is not None:
                item['illustration'] = item_illustration
        if design_language == 'Screenshot Editorial':
            rendered.append(_render_screenshot_editorial_card(
                card, i, width, height, sx, sy, sf, font_path,
                primary, secondary, tertiary, ink,
            ))
            continue
        im = Image.new('RGB', (width, height), background)
        draw = ImageDraw.Draw(im)
        if named_style:
            _blog_named_style_decor(
                draw, named_style, width, height, primary, secondary, tertiary, ink,
            )
        else:
            _blog_family_decor(draw, visual_family, width, height, primary, secondary, tertiary, ink)
        role = ('먼저 답', '왜 그런지', '같은 조건 비교', '지금 확인', '마지막 판단')[i - 1]
        _blog_style_panel(draw, box((62, 48, 390, 102)), named_style, fill=primary, outline=None,
                          width=size(2), radius=size(20))
        _blog_text(draw, role, box((84, 60, 370, 92)), font_path, size(25), accent_text, 1)
        _blog_text(draw, f'{i:02d} / 05', box((868, 60, 1018, 92)), font_path, size(25), ink, 1)
        if visual_family == 'contract_notebook':
            _blog_style_panel(draw, box((54, 128, 1026, 300)), named_style,
                              fill=_blog_style_fill('#F2EAF6', named_style), outline=None,
                              width=size(2), radius=size(18))
        _blog_text(draw, headline, box((62, 140, 1018, 292)), font_path, size(title_size), ink, 2, title=True)
        _blog_text(draw, copy, box((64, 306, 1016, 396)), font_path, size(copy_size), ink, 2)

        if layout == 'cover':
            # A concrete lifestyle scene, not a floating decorative icon.
            _blog_style_panel(draw, box((160, 414, 920, 814)), named_style,
                              fill=_blog_style_fill('#F5F2FC', named_style), outline=ink,
                              width=size(4), radius=size(34))
            draw.arc(box((270, 456, 530, 675)), 175, 356, fill=secondary, width=size(8))
            _blog_symbol(draw, illustration, point((455, 618)), size(270), ink, surface)
            companion = _blog_companion_symbol(illustration)
            draw.ellipse(box((620, 536, 828, 744)), fill=tertiary)
            _blog_symbol(draw, companion, point((724, 640)), size(128), ink, surface)
            _blog_arrow(draw, point((585, 650)), point((640, 650)), ink)
            for j, item in enumerate(items):
                x = 64 + j * 500
                _blog_style_panel(draw, box((x, 824, x + 452, 984)), named_style,
                                  fill=(secondary, tertiary)[j], outline=None,
                                  width=size(2), radius=size(24))
                _blog_text(draw, item['label'], box((x + 24, 846, x + 428, 892)), font_path, size(34), ink, 1, title=True)
                _blog_text(draw, item['detail'], box((x + 24, 907, x + 428, 970)), font_path, size(27), ink, 2)
        elif layout == 'flow':
            _blog_style_panel(draw, box((48, 405, 1032, 974)), named_style,
                              fill=_blog_style_fill('#FBFAFE', named_style), outline=ink,
                              width=size(4), radius=size(34))
            for ring_x in range(150, 950, 125):
                _blog_style_panel(draw, box((ring_x, 388, ring_x + 26, 432)), named_style,
                                  fill=ink, outline=None, width=size(1), radius=size(10))
            step_symbols = _blog_flow_symbols(illustration)
            for j, item in enumerate(items):
                x = 72 + j * 320
                color = (primary, secondary, tertiary)[j]
                _blog_style_panel(draw, box((x, 470, x + 296, 938)), named_style,
                                  fill=color, outline=None, width=size(2), radius=size(26))
                draw.ellipse(box((x + 18, 488, x + 82, 552)), fill=surface, outline=ink, width=size(3))
                _blog_text(draw, str(j + 1), box((x + 39, 500, x + 70, 540)), font_path, size(29), ink, 1, title=True)
                _blog_symbol(draw, step_symbols[j], point((x + 148, 642)), size(155), ink, surface)
                _blog_text(draw, item['label'], box((x + 22, 744, x + 274, 820)), font_path, size(29), ink, 2, title=True)
                _blog_text(draw, item['detail'], box((x + 22, 826, x + 274, 926)), font_path, size(21), ink, 3)
                if j < 2:
                    _blog_arrow(draw, point((x + 298, 684)), point((x + 318, 684)), ink)
        elif layout == 'comparison':
            symbols = _blog_distinct_item_symbols(items, illustration)
            for j, item in enumerate(items):
                x = 64 + j * 500
                color = (primary, secondary)[j]
                _blog_style_panel(draw, box((x, 426, x + 452, 970)), named_style,
                                  fill=color, outline=ink, width=size(4), radius=size(30))
                _blog_style_panel(draw, box((x + 26, 454, x + 426, 560)), named_style,
                                  fill=surface, outline=None, width=size(1), radius=size(22))
                _blog_text(draw, item['label'], box((x + 48, 478, x + 404, 548)), font_path, size(34), ink, 2, title=True)
                _blog_symbol(draw, symbols[j], point((x + 226, 671)), size(175), ink, surface)
                _blog_text(draw, item['detail'], box((x + 34, 788, x + 418, 936)), font_path, size(28), ink, 4)
            draw.ellipse(box((506, 625, 574, 693)), fill=surface, outline=ink, width=size(3))
            _blog_text(draw, 'VS', box((522, 641, 560, 681)), font_path, size(26), ink, 1, title=True)
        elif layout == 'checklist':
            _blog_style_panel(draw, box((48, 405, 1032, 980)), named_style,
                              fill=_blog_style_fill('#F9FBFC', named_style), outline=ink,
                              width=size(4), radius=size(34))
            _blog_style_panel(draw, box((420, 388, 660, 445)), named_style,
                              fill=primary, outline=ink, width=size(3), radius=size(18))
            item_height = 130 if len(items) == 4 else 168
            symbols = _blog_distinct_item_symbols(items, illustration)
            for j, item in enumerate(items):
                y = 452 + j * item_height
                color = (primary, secondary, tertiary, '#DCE8F5')[j]
                _blog_style_panel(draw, box((70, y + 5, 1010, y + item_height - 10)), named_style,
                                  fill=color, outline=None, width=size(2), radius=size(22))
                draw.ellipse(box((92, y + 22, 192, y + 122)), fill=surface, outline=ink, width=size(3))
                _blog_symbol(draw, symbols[j], point((142, y + 72)), size(62), ink, surface)
                _blog_text(draw, item['label'], box((220, y + 18, 985, y + 61)), font_path, size(31), ink, 1, title=True)
                _blog_text(draw, item['detail'], box((220, y + 68, 985, y + item_height - 19)), font_path, size(26), ink, 2)
                if j < len(items) - 1:
                    draw.line(box((175, y + item_height - 4, 905, y + item_height - 4)), fill='#FFFFFF', width=size(3))
        else:  # decision: three visually distinct branches, not a report timeline.
            symbols = _blog_distinct_item_symbols(items, illustration)
            _blog_style_panel(draw, box((384, 410, 696, 474)), named_style,
                              fill=surface, outline=ink, width=size(4), radius=size(22))
            _blog_text(draw, '내 상황은 어디에 가까울까?', box((404, 430, 686, 460)), font_path, size(19), ink, 1, title=True)
            branch_centers = (210, 540, 870)
            for cx in branch_centers:
                draw.line((point((540, 474)), point((cx, 523))), fill=ink, width=size(4))
            for j, item in enumerate(items):
                x = 64 + j * 330
                color = (primary, secondary, tertiary)[j]
                _blog_style_panel(draw, box((x, 520, x + 292, 950)), named_style,
                                  fill=color, outline=ink, width=size(4), radius=size(28))
                draw.ellipse(box((x + 76, 555, x + 216, 695)), fill=surface)
                _blog_symbol(draw, symbols[j], point((x + 146, 625)), size(90), ink, surface)
                _blog_text(draw, item['label'], box((x + 20, 724, x + 272, 804)), font_path, size(29), ink, 2, title=True)
                _blog_text(draw, item['detail'], box((x + 20, 818, x + 272, 930)), font_path, size(24), ink, 4)
        draw.line(box((64, 1012, 1016, 1012)), fill=ink, width=size(2))
        _blog_text(draw, '이해를 돕는 설명 도식 · 실제 제품 사진 아님', box((64, 1030, 1016, 1065)), font_path, size(22), ink, 1)
        rendered.append(im)

    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for i, im in enumerate(rendered, 1):
        path = out_dir / f'card_{i:02d}.png'
        im.save(path)
        paths.append(path)
    return paths


def _render_screenshot_editorial_card(
    card: dict[str, Any], index: int, width: int, height: int,
    sx: float, sy: float, sf: float, font_path: str | None,
    primary: str, secondary: str, tertiary: str, ink: str,
) -> Image.Image:
    """Render Screenshot Editorial as a cover-first screen story.

    This intentionally bypasses the generic card/pill renderer.  The screen is
    the primary image field, type lives in a separate editorial zone, and each
    role receives a different screen composition.
    """
    def box(values):
        return tuple(int(v * (sx if i % 2 == 0 else sy)) for i, v in enumerate(values))

    def point(values):
        return int(values[0] * sx), int(values[1] * sy)

    def size(value):
        return max(1, int(value * sf))

    def window(coords, *, dark=False, split=False):
        x1, y1, x2, y2 = coords
        fill = '#161D28' if dark else '#F9FBFF'
        draw.rounded_rectangle(coords, radius=size(12), fill=fill, outline=ink, width=size(3))
        draw.line((x1, y1 + size(44), x2, y1 + size(44)), fill=ink, width=size(2))
        for j, color in enumerate((tertiary, secondary, primary)):
            cx = x1 + size(23 + j * 24)
            cy = y1 + size(22)
            draw.ellipse((cx - size(6), cy - size(6), cx + size(6), cy + size(6)), fill=color)
        if split:
            mid = int((x1 + x2) / 2)
            draw.rectangle((x1 + size(2), y1 + size(46), mid, y2 - size(2)), fill='#161D28')
            draw.rectangle((mid, y1 + size(46), x2 - size(2), y2 - size(2)), fill='#F9FBFF')

    def code_lines(area, dark=True):
        x1, y1, x2, y2 = area
        colors = (primary, secondary, tertiary, '#91A0BA')
        usable = max(x2 - x1, 1)
        for row in range(7):
            y = y1 + size(row * 34)
            lead = size((row % 3) * 18)
            length = int(usable * (0.45 + (row % 4) * 0.11))
            draw.rounded_rectangle((x1 + lead, y, min(x2, x1 + lead + length), y + size(9)),
                                   radius=size(4), fill=colors[row % len(colors)])

    def chat_bubbles(area):
        x1, y1, x2, y2 = area
        colors = ('#DDE5FF', '#DDF5EE', '#F5E1E8')
        for row in range(4):
            w = int((x2 - x1) * (0.58 if row % 2 == 0 else 0.46))
            left = x1 + (size(18) if row % 2 == 0 else (x2 - x1 - w - size(18)))
            top = y1 + size(row * 70)
            draw.rounded_rectangle((left, top, left + w, top + size(44)), radius=size(12), fill=colors[row % 3])

    background = Image.new('RGB', (width, height), '#EEF1F6')
    draw = ImageDraw.Draw(background)
    # Fine editorial grain/grid, deliberately cooler and quieter than the
    # generic rounded-card surface.
    for x in range(size(28), width, size(38)):
        draw.line((x, 0, x, height), fill='#E2E6EE', width=1)
    draw.rectangle(box((0, 0, 1080, 28)), fill=ink)
    draw.text(point((54, 62)), 'SCREENSHOT EDITORIAL', font=load_font(font_path, size(19)), fill=ink)
    draw.text(point((1018, 62)), f'{index:02d} / 05', font=load_font(font_path, size(19)), fill=ink, anchor='ra')
    draw.line(box((54, 94, 1026, 94)), fill='#B8C1D2', width=size(2))

    layout = card['layout']
    headline = card['headline']
    copy = card['copy']
    items = card['items']
    illustration = card['illustration']

    if layout == 'cover':
        # Hero screen first; headline is a separate lower editorial field.
        window(box((92, 130, 988, 520)), split=True)
        code_lines(box((140, 214, 500, 438)))
        chat_bubbles(box((610, 205, 940, 465)))
        draw.line(box((540, 176, 540, 490)), fill=primary, width=size(8))
        _blog_text(draw, headline, box((74, 574, 1006, 710)), font_path, size(58), ink, 2, title=True)
        draw.rectangle(box((74, 728, 224, 740)), fill=primary)
        _blog_text(draw, copy, box((74, 758, 1006, 824)), font_path, size(27), ink, 2)
        for j, item in enumerate(items):
            x = 74 + j * 492
            draw.text(point((x, 850)), f'0{j + 1}', font=load_font(font_path, size(18)), fill=primary)
            _blog_text(draw, item['label'], box((x, 880, x + 430, 922)), font_path, size(24), ink, 1, title=True)
            _blog_text(draw, item['detail'], box((x, 932, x + 430, 996)), font_path, size(20), '#606A7C', 2)
    elif layout == 'flow':
        _blog_text(draw, headline, box((60, 128, 1018, 246)), font_path, size(52), ink, 2, title=True)
        _blog_text(draw, copy, box((64, 258, 1016, 320)), font_path, size(24), '#596476', 2)
        window(box((62, 346, 1018, 800)), dark=True)
        code_lines(box((116, 452, 646, 696)))
        draw.rectangle(box((704, 394, 970, 756)), fill='#F8FAFD')
        symbols = _blog_flow_symbols(illustration)
        for j, item in enumerate(items):
            y = 424 + j * 108
            draw.text(point((732, y)), f'0{j + 1}', font=load_font(font_path, size(18)), fill=primary)
            _blog_symbol(draw, symbols[j], point((760, y + 65)), size(52), ink, '#FFFFFF')
            _blog_text(draw, item['label'], box((800, y + 34, 950, y + 75)), font_path, size(20), ink, 2, title=True)
            if j < 2:
                draw.line(box((748, y + 105, 944, y + 105)), fill=secondary, width=size(3))
        _blog_arrow(draw, point((412, 840)), point((690, 840)), primary)
    elif layout == 'comparison':
        _blog_text(draw, headline, box((60, 128, 1018, 246)), font_path, size(52), ink, 2, title=True)
        _blog_text(draw, copy, box((64, 258, 1016, 320)), font_path, size(24), '#596476', 2)
        window(box((62, 346, 1018, 770)), split=True)
        code_lines(box((102, 454, 500, 680)))
        chat_bubbles(box((610, 438, 970, 702)))
        for j, item in enumerate(items):
            x = 92 + j * 488
            color = secondary if j == 0 else primary
            draw.rectangle(box((x, 806, x + 420, 818)), fill=color)
            _blog_text(draw, item['label'], box((x, 838, x + 420, 880)), font_path, size(24), ink, 1, title=True)
            _blog_text(draw, item['detail'], box((x, 892, x + 420, 982)), font_path, size(20), '#606A7C', 3)
    elif layout == 'checklist':
        _blog_text(draw, headline, box((60, 128, 1018, 246)), font_path, size(52), ink, 2, title=True)
        window(box((62, 284, 1018, 824)), dark=False)
        # One screen, three distinct focus zones.  No repeated floating cards.
        draw.rectangle(box((64, 330, 300, 822)), fill='#DDE4F6')
        symbols = _blog_distinct_item_symbols(items, illustration)
        for j, item in enumerate(items):
            y = 356 + j * 112
            color = (primary, secondary, tertiary, '#9BB4D8')[j]
            draw.ellipse(box((102, y, 174, y + 72)), fill='#FFFFFF', outline=color, width=size(4))
            _blog_symbol(draw, symbols[j], point((138, y + 36)), size(42), ink, '#FFFFFF')
            draw.line(box((174, y + 36, 336, y + 36)), fill=color, width=size(5))
            _blog_text(draw, item['label'], box((356, y - 2, 944, y + 45)), font_path, size(27), ink, 1, title=True)
            _blog_text(draw, item['detail'], box((356, y + 50, 944, y + 112)), font_path, size(22), '#606A7C', 2)
        _blog_text(draw, copy, box((64, 860, 1016, 924)), font_path, size(23), ink, 2)
    else:  # decision
        _blog_text(draw, headline, box((60, 128, 1018, 246)), font_path, size(52), ink, 2, title=True)
        _blog_text(draw, copy, box((64, 258, 1016, 320)), font_path, size(24), '#596476', 2)
        window(box((62, 346, 1018, 756)), split=True)
        code_lines(box((104, 456, 500, 656)))
        chat_bubbles(box((610, 438, 968, 680)))
        draw.line(box((540, 386, 540, 716)), fill=primary, width=size(7))
        for j, item in enumerate(items):
            x = 64 + j * 330
            draw.text(point((x, 800)), f'0{j + 1}', font=load_font(font_path, size(18)), fill=primary)
            _blog_text(draw, item['label'], box((x, 832, x + 286, 880)), font_path, size(22), ink, 2, title=True)
            _blog_text(draw, item['detail'], box((x, 892, x + 286, 980)), font_path, size(18), '#606A7C', 4)

    # Redraw the masthead last so large Korean title ascenders can never
    # visually swallow the editorial identity or page number.
    draw.rectangle(box((0, 0, 1080, 108)), fill='#EEF1F6')
    draw.rectangle(box((0, 0, 1080, 28)), fill=ink)
    draw.text(point((54, 62)), 'SCREENSHOT EDITORIAL', font=load_font(font_path, size(19)), fill=ink)
    draw.text(point((1018, 62)), f'{index:02d} / 05', font=load_font(font_path, size(19)), fill=ink, anchor='ra')
    draw.line(box((54, 94, 1026, 94)), fill='#B8C1D2', width=size(2))
    draw.line(box((64, 1012, 1016, 1012)), fill='#B8C1D2', width=size(2))
    _blog_text(draw, '화면을 단순화한 설명용 편집 이미지 · 실제 앱 화면 아님',
               box((64, 1030, 1016, 1065)), font_path, size(18), '#6D7685', 1)
    return background


def _blog_style_fill(fill: str, style: dict[str, Any] | None) -> str:
    if not style:
        return fill
    if fill.upper() in {'#FFFFFF', '#FBFAFE', '#F9FBFC', '#F5F2FC', '#F2EAF6'}:
        return style['surface']
    return fill


def _blog_style_panel(
    draw, coords, style: dict[str, Any] | None, *, fill: str, outline: str | None,
    width: int, radius: int,
) -> None:
    """Draw a panel using the selected named design grammar.

    A missing style deliberately preserves the legacy rounded renderer so old
    reviewed manifests can be reproduced byte-for-byte during recovery.
    """
    if not style:
        draw.rounded_rectangle(coords, radius=radius, fill=fill, outline=outline, width=width)
        return
    panel = style['panel']
    border = max(width, int(style['border_width']))
    chosen_radius = min(radius, int(style['radius']))
    if panel == 'brutal':
        x1, y1, x2, y2 = coords
        offset = max(6, border * 2)
        draw.rectangle((x1 + offset, y1 + offset, x2 + offset, y2 + offset), fill=style['palette'][3])
        draw.rectangle(coords, fill=fill, outline=outline or style['palette'][3], width=border)
    elif panel in {'rule', 'sharp', 'modular', 'split'}:
        draw.rectangle(coords, fill=fill, outline=outline, width=border)
        if panel == 'rule':
            draw.line((coords[0], coords[3], coords[2], coords[3]), fill=outline or style['palette'][3], width=border)
    elif panel == 'window':
        draw.rounded_rectangle(coords, radius=max(2, chosen_radius), fill=fill,
                               outline=outline or style['palette'][3], width=border)
        bar_y = min(coords[3], coords[1] + max(10, border * 5))
        draw.line((coords[0] + border, bar_y, coords[2] - border, bar_y),
                  fill=outline or style['palette'][3], width=max(1, border))
    elif panel == 'tape':
        draw.rectangle(coords, fill=fill, outline=outline or style['palette'][3], width=border)
        tape = style['palette'][1]
        span = max(18, int((coords[2] - coords[0]) * .12))
        center = int((coords[0] + coords[2]) / 2)
        draw.rectangle((center - span, coords[1] - border * 2, center + span, coords[1] + border * 3), fill=tape)
    elif panel == 'chrome':
        draw.rounded_rectangle(coords, radius=max(4, chosen_radius), fill=fill,
                               outline=outline or style['palette'][1], width=border)
        draw.line((coords[0] + border * 3, coords[1] + border * 3,
                   coords[2] - border * 3, coords[1] + border * 3),
                  fill='#FFFFFF', width=max(1, border))
    else:  # soft and bento
        draw.rounded_rectangle(coords, radius=max(6, chosen_radius), fill=fill,
                               outline=outline, width=border)


def _blog_named_style_decor(draw, style, width, height, primary, secondary, tertiary, ink):
    """Functional style cues; these are never decorative product claims."""
    motif = style['motif']
    if motif == 'swiss':
        draw.line((int(width * .055), 0, int(width * .055), height), fill='#E3E5E8', width=2)
        draw.rectangle((int(width * .88), int(height * .12), int(width * .94), int(height * .18)), fill=primary)
    elif motif == 'retro_ui':
        draw.rectangle((0, 0, width, int(height * .035)), fill=primary)
        for x in (.025, .05, .075):
            draw.rectangle((int(width * x), int(height * .012), int(width * (x + .012)), int(height * .024)), fill=ink)
    elif motif == 'bento':
        draw.rounded_rectangle((int(width * .72), int(height * .025), int(width * .96), int(height * .09)), radius=12, fill=secondary)
        draw.rounded_rectangle((int(width * .88), int(height * .31), int(width * .98), int(height * .38)), radius=12, fill=tertiary)
    elif motif == 'magazine':
        draw.line((int(width * .04), int(height * .02), int(width * .04), int(height * .98)), fill=ink, width=4)
        draw.line((int(width * .045), int(height * .115), int(width * .96), int(height * .115)), fill=ink, width=2)
    elif motif == 'brutal':
        draw.rectangle((int(width * .84), int(height * .02), int(width * .98), int(height * .09)), fill=secondary, outline=ink, width=4)
        draw.rectangle((int(width * .02), int(height * .86), int(width * .08), int(height * .96)), fill=tertiary, outline=ink, width=4)
    elif motif == 'japanese':
        draw.line((int(width * .94), int(height * .04), int(width * .94), int(height * .36)), fill=primary, width=3)
        for y in (.08, .12, .16):
            draw.ellipse((int(width * .925), int(height * y), int(width * .955), int(height * (y + .03))), fill=secondary)
    elif motif == 'quiet':
        draw.rectangle((int(width * .025), int(height * .025), int(width * .975), int(height * .975)), outline=primary, width=1)
    elif motif == 'newspaper':
        draw.line((int(width * .04), int(height * .105), int(width * .96), int(height * .105)), fill=ink, width=5)
        draw.line((int(width * .04), int(height * .12), int(width * .96), int(height * .12)), fill=ink, width=1)
    elif motif == 'technical':
        for x in range(0, width, max(40, width // 18)):
            draw.line((x, 0, x, height), fill='#D9E8F1', width=1)
        for y in range(0, height, max(40, height // 18)):
            draw.line((0, y, width, y), fill='#D9E8F1', width=1)
        draw.line((int(width * .03), int(height * .10), int(width * .03), int(height * .31)), fill=primary, width=3)
    elif motif == 'screenshot':
        draw.rounded_rectangle((int(width * .02), int(height * .015), int(width * .98), int(height * .09)), radius=12, fill=style['surface'], outline=ink, width=2)
        for x, color in zip((.045, .072, .099), (tertiary, secondary, primary)):
            draw.ellipse((int(width * x), int(height * .04), int(width * (x + .018)), int(height * .058)), fill=color)
    elif motif == 'prompt':
        draw.rounded_rectangle((int(width * .68), int(height * .03), int(width * .97), int(height * .09)), radius=12, fill=style['surface'], outline=primary, width=2)
        draw.line((int(width * .71), int(height * .06), int(width * .73), int(height * .06)), fill=primary, width=4)
    elif motif == 'terminal':
        draw.text((int(width * .035), int(height * .025)), '>_', fill=primary)
        draw.line((int(width * .035), int(height * .105), int(width * .965), int(height * .105)), fill=secondary, width=2)
    elif motif == 'fluorescent':
        draw.rectangle((int(width * .69), int(height * .055), int(width * .96), int(height * .072)), fill=primary)
        draw.rectangle((int(width * .88), int(height * .32), int(width * .97), int(height * .34)), fill=secondary)
    elif motif == 'soft_swiss':
        draw.ellipse((int(width * .82), int(height * .03), int(width * .96), int(height * .17)), fill=secondary)
        draw.line((int(width * .04), int(height * .12), int(width * .24), int(height * .12)), fill=primary, width=5)
    elif motif == 'index':
        for i, color in enumerate((primary, secondary, tertiary)):
            y1 = int(height * (.22 + i * .08))
            draw.rectangle((int(width * .94), y1, width, y1 + int(height * .055)), fill=color)
    elif motif == 'cinematic':
        draw.rectangle((0, 0, width, int(height * .035)), fill='#05070B')
        draw.rectangle((0, int(height * .965), width, height), fill='#05070B')
        draw.line((int(width * .06), int(height * .11), int(width * .94), int(height * .11)), fill=primary, width=2)
    elif motif == 'zine':
        draw.rectangle((int(width * .77), int(height * .035), int(width * .94), int(height * .065)), fill=secondary)
        draw.line((int(width * .04), int(height * .13), int(width * .22), int(height * .10)), fill=primary, width=6)
    elif motif == 'split':
        draw.rectangle((0, 0, int(width * .5), int(height * .11)), fill=primary)
        draw.rectangle((int(width * .5), 0, width, int(height * .11)), fill=secondary)
        draw.line((int(width * .5), int(height * .11), int(width * .5), int(height * .38)), fill=ink, width=3)
    elif motif == 'chrome':
        draw.ellipse((int(width * .82), int(height * .025), int(width * .97), int(height * .175)), outline=secondary, width=8)
        draw.ellipse((int(width * .855), int(height * .06), int(width * .935), int(height * .14)), outline='#FFFFFF', width=3)
    else:  # modular
        draw.rectangle((int(width * .74), int(height * .025), int(width * .86), int(height * .085)), fill=primary)
        draw.rectangle((int(width * .87), int(height * .025), int(width * .97), int(height * .085)), fill=secondary)
        draw.rectangle((int(width * .90), int(height * .30), int(width * .98), int(height * .38)), fill=tertiary)


def _blog_family_decor(draw, family, width, height, primary, secondary, tertiary, ink):
    """Small functional editorial cues distilled from the approved references."""
    if family == 'soft_scene':
        draw.ellipse((int(width * .64), int(height * .37), int(width * 1.02), int(height * .78)), fill='#F5F3FB')
        draw.arc((int(width * .74), int(height * .18), int(width * .96), int(height * .40)), 190, 285, fill=secondary, width=4)
    elif family == 'playful_diagram':
        draw.line((int(width * .035), int(height * .12), int(width * .075), int(height * .10)), fill=secondary, width=7)
        draw.line((int(width * .93), int(height * .34), int(width * .97), int(height * .31)), fill=tertiary, width=7)
    elif family == 'contract_notebook':
        draw.line((int(width * .05), int(height * .025), int(width * .95), int(height * .025)), fill=primary, width=8)
    else:  # clipboard_checklist
        draw.rounded_rectangle((int(width * .018), int(height * .018), int(width * .982), int(height * .982)), radius=24, outline=ink, width=4)
        draw.rounded_rectangle((int(width * .43), int(height * .012), int(width * .57), int(height * .052)), radius=12, fill=primary, outline=ink, width=3)


def _blog_required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'Missing {field}')
    return value.strip()


def _blog_text(draw, text, box, font_path, size, fill, max_lines, title=False):
    bundled = Path(__file__).resolve().parents[1] / 'assets/fonts/Cafe24Ssurround-v2.0/Cafe24Ssurround-v2.0.ttf'
    font = load_font(str(bundled) if title and bundled.exists() else font_path, size)
    if not isinstance(font, ImageFont.FreeTypeFont):
        raise ValueError('A readable Korean TrueType/OpenType font is required for blog cards')
    left, top, right, bottom = box
    lines: list[str] = []
    current = ''
    for ch in str(text):
        if ch == '\n':
            lines.append(current)
            current = ''
        elif draw.textlength(current + ch, font=font) > right - left:
            if not current:
                raise ValueError('Blog card text character exceeds its available width')
            word_boundary = current.rfind(' ')
            if word_boundary > 0:
                lines.append(current[:word_boundary])
                current = current[word_boundary + 1:] + ch
            else:
                lines.append(current)
                current = ch
        else:
            current += ch
    if current:
        lines.append(current)
    line_height = size + 9
    if len(lines) > max_lines or len(lines) * line_height - 9 > bottom - top:
        raise ValueError(f'Blog card text overflow ({max_lines} lines allowed): {text!r}')
    for line_index, line in enumerate(lines):
        draw.text((left, top + line_index * line_height), line, font=font, fill=fill, anchor='lt')


def _blog_arrow(draw, start, end, color):
    draw.line((start, end), fill=color, width=5)
    x, y = end
    draw.line(((x - 10, y - 10), (x, y), (x + 10, y - 10)), fill=color, width=5)


def _blog_companion_symbol(symbol: str) -> str:
    """Return a second concrete object so the cover reads as a scene."""
    return {
        'garment': 'steamer',
        'steamer': 'garment',
        'wifi': 'home',
        'home': 'wifi',
        'measurement': 'home',
        'bubbles': 'appliance',
        'laundry': 'care_label',
        'document': 'care_label',
        'appliance': 'document',
        'diagram': 'document',
    }.get(symbol, 'document')


def _blog_flow_symbols(symbol: str) -> tuple[str, str, str]:
    """Three related, distinct objects for a real process rather than numbers alone."""
    return {
        'steamer': ('home', 'steamer', 'garment'),
        'garment': ('garment', 'steamer', 'wrinkle'),
        'wifi': ('home', 'wifi', 'measurement'),
        'bubbles': ('document', 'bubbles', 'appliance'),
        'laundry': ('care_label', 'laundry', 'garment'),
        'measurement': ('home', 'measurement', 'document'),
        'appliance': ('document', 'appliance', 'care_label'),
        'document': ('home', 'document', 'care_label'),
    }.get(symbol, ('document', symbol, 'diagram'))


def _blog_item_symbol(item: dict[str, Any], fallback: str) -> str:
    text = f"{item.get('label', '')} {item.get('detail', '')}".lower()
    rules = (
        (('다리미', '다림질판'), 'iron'),
        (('스티머', '스팀'), 'steamer'),
        (('라벨', '설명서', '지침', '계약', '표시'), 'care_label'),
        (('치수', '폭 ', '깊이', '공간', '설치'), 'measurement'),
        (('셔츠', '재킷', '니트', '옷', '의류', '옷감'), 'garment'),
        (('주름', '구김'), 'wrinkle'),
        (('와이파이', '통신', '공유기'), 'wifi'),
        (('세제', '거품'), 'bubbles'),
        (('세탁', '빨래', '침구'), 'laundry'),
        (('집', '방', '생활 공간'), 'home'),
        (('가전', '기기', '식기세척기'), 'appliance'),
    )
    for needles, symbol in rules:
        if any(needle in text for needle in needles):
            return symbol
    return fallback


def _blog_distinct_item_symbols(items: list[dict[str, Any]], fallback: str) -> list[str]:
    """Prefer semantic item icons and forbid a comparison from repeating one generic mark."""
    alternatives = ('document', 'measurement', 'home', 'garment', 'care_label', 'diagram')
    symbols: list[str] = []
    for index, item in enumerate(items):
        symbol = item.get('illustration') or _blog_item_symbol(item, fallback)
        if symbol in symbols:
            symbol = next(candidate for candidate in alternatives if candidate not in symbols)
        symbols.append(symbol)
    return symbols


def _blog_symbol(draw, symbol, center, size, ink, paper):
    """Original abstract explanatory marks, not drawings of particular models."""
    x, y = center
    r = size / 2
    if symbol == 'bubbles':
        for dx, dy, radius in ((-0.55, 0.2, 0.58), (0.35, 0.35, 0.7), (-0.04, -0.55, 0.65), (0.81, -0.55, 0.25)):
            cx, cy, rr = x + dx * r, y + dy * r, r * radius
            draw.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), fill=paper, outline=ink, width=5)
            draw.arc((cx - rr * .6, cy - rr * .6, cx + rr * .6, cy + rr * .6), 195, 260, fill=ink, width=3)
    elif symbol == 'wifi':
        for scale in (.5, .82, 1.14):
            rr = r * scale
            draw.arc((x - rr, y - rr, x + rr, y + rr), 214, 326, fill=ink, width=9)
        draw.ellipse((x - 11, y - 3, x + 11, y + 19), fill=ink)
        # A wall boundary explains coverage without pretending to show a router.
        draw.line((x + r * 1.45, y - r, x + r * 1.45, y + r * .8), fill=ink, width=8)
    elif symbol == 'laundry':
        draw.rounded_rectangle((x - r, y - r * .7, x + r, y + r * .7), radius=18, fill=paper, outline=ink, width=5)
        for dx in (-.48, 0, .48):
            draw.line((x + dx * r, y - r * .65, x + dx * r, y + r * .65), fill=ink, width=3)
        draw.line((x - r * .92, y + r * .35, x + r * .9, y + r * .35), fill=ink, width=3)
    elif symbol == 'appliance':
        draw.rounded_rectangle((x - r * .82, y - r, x + r * .82, y + r), radius=18, fill=paper, outline=ink, width=5)
        draw.ellipse((x - r * .48, y - r * .38, x + r * .48, y + r * .58), outline=ink, width=6)
        draw.ellipse((x - r * .55, y - r * .78, x - r * .43, y - r * .66), fill=ink)
        draw.ellipse((x - r * .28, y - r * .78, x - r * .16, y - r * .66), fill=ink)
    elif symbol == 'measurement':
        draw.line((x - r, y, x + r, y), fill=ink, width=6)
        draw.line((x - r, y - r * .28, x - r, y + r * .28), fill=ink, width=6)
        draw.line((x + r, y - r * .28, x + r, y + r * .28), fill=ink, width=6)
        for dx in (-.65, -.3, .05, .4, .75):
            draw.line((x + dx * r, y, x + dx * r, y + r * .24), fill=ink, width=3)
        draw.line((x - r, y - r * .5, x + r, y - r * .5), fill=ink, width=4)
    elif symbol == 'home':
        draw.line((x - r, y - r * .15, x, y - r, x + r, y - r * .15), fill=ink, width=6)
        draw.rounded_rectangle((x - r * .78, y - r * .15, x + r * .78, y + r), radius=10, fill=paper, outline=ink, width=5)
        draw.rectangle((x - r * .18, y + r * .36, x + r * .18, y + r), outline=ink, width=4)
    elif symbol == 'diagram':
        points = ((x - r * .65, y + r * .45), (x, y - r * .55), (x + r * .65, y + r * .45))
        _blog_arrow(draw, points[0], points[1], ink)
        _blog_arrow(draw, points[1], points[2], ink)
        for cx, cy in points:
            rr = r * .28
            draw.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), fill=paper, outline=ink, width=5)
    elif symbol == 'garment':
        draw.arc((x - r * .28, y - r, x + r * .28, y - r * .45), 170, 350, fill=ink, width=5)
        draw.line((x, y - r * .48, x - r * .88, y + r * .12, x + r * .88, y + r * .12, x, y - r * .48), fill=ink, width=5)
        draw.rounded_rectangle((x - r * .72, y + r * .12, x + r * .72, y + r * .82), radius=14, fill=paper, outline=ink, width=5)
    elif symbol == 'steamer':
        draw.rounded_rectangle((x - r * .28, y - r * .05, x + r * .28, y + r), radius=16, fill=paper, outline=ink, width=5)
        draw.line((x - r * .2, y - r * .05, x - r * .55, y - r * .55, x + r * .15, y - r * .72), fill=ink, width=7)
        for offset in (-.42, 0, .42):
            draw.arc((x + r * offset, y - r * 1.18, x + r * (offset + .36), y - r * .55), 110, 255, fill=ink, width=4)
    elif symbol == 'iron':
        draw.rounded_rectangle((x - r * .72, y + r * .18, x + r * .72, y + r * .62), radius=14, fill=paper, outline=ink, width=5)
        draw.polygon(((x - r * .72, y + r * .18), (x + r * .28, y - r * .72),
                      (x + r * .65, y + r * .18)), fill=paper, outline=ink)
        draw.arc((x - r * .12, y - r * .72, x + r * .52, y - r * .05), 180, 350, fill=ink, width=6)
        for dx in (-.34, 0, .34):
            draw.ellipse((x + dx * r - 4, y + r * .34 - 4, x + dx * r + 4, y + r * .34 + 4), fill=ink)
    elif symbol == 'care_label':
        draw.rounded_rectangle((x - r * .72, y - r, x + r * .72, y + r), radius=12, fill=paper, outline=ink, width=5)
        draw.line((x - r * .42, y - r * .48, x + r * .42, y - r * .48), fill=ink, width=4)
        draw.ellipse((x - r * .4, y - r * .12, x - r * .05, y + r * .22), outline=ink, width=4)
        draw.polygon(((x + r * .12, y + r * .22), (x + r * .28, y - r * .14),
                      (x + r * .48, y + r * .22)), outline=ink)
        draw.line((x - r * .42, y + r * .52, x + r * .42, y + r * .52), fill=ink, width=4)
    elif symbol == 'wrinkle':
        draw.rounded_rectangle((x - r, y - r * .76, x + r, y + r * .76), radius=16, fill=paper, outline=ink, width=5)
        for offset in (-.42, 0, .42):
            points = []
            for step in range(7):
                px = x - r * .76 + step * r * .25
                py = y + offset * r + (r * .10 if step % 2 else -r * .10)
                points.append((px, py))
            draw.line(points, fill=ink, width=4)
    else:
        draw.rounded_rectangle((x - r * .7, y - r, x + r * .7, y + r), radius=12, fill=paper, outline=ink, width=5)
        for dy in (-.48, 0, .48):
            draw.line((x - r * .4, y + r * dy, x + r * .4, y + r * dy), fill=ink, width=5)


def load_font(font_path: str | None, size: int):
    candidates = [font_path] if font_path else []
    candidates += [
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
        '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc',
        '/System/Library/Fonts/AppleSDGothicNeo.ttc',
        'C:/Windows/Fonts/malgun.ttf',
    ]
    for cand in candidates:
        if cand and Path(cand).exists():
            return ImageFont.truetype(cand, size=size)
    return ImageFont.load_default()


def draw_multiline(draw, text: str, xy: tuple[int, int], max_width: int, font, fill: str, spacing: int = 10):
    lines: list[str] = []
    current = ''
    for ch in text:
        trial = current + ch
        box = draw.textbbox((0, 0), trial, font=font)
        if box[2] - box[0] > max_width and current:
            lines.append(current)
            current = ch
        else:
            current = trial
    if current:
        lines.append(current)
    draw.multiline_text(xy, '\n'.join(lines), font=font, fill=fill, spacing=spacing)


def _subtitle_phrases(text: str, max_chars: int = 22) -> list[str]:
    """Split exact narration into short, readable speech-paced cues."""
    text = ' '.join(text.split())
    if not text:
        return []
    phrases: list[str] = []
    current = ''
    for char in text:
        current += char
        boundary = char in '。！？!?….,，、' or len(current) >= max_chars
        if boundary and current.strip():
            phrases.append(current.strip())
            current = ''
    if current.strip():
        phrases.append(current.strip())
    return phrases


def make_srt(scenes: list[dict[str, Any]], path: Path) -> Path:
    cursor = 0.0
    chunks = []
    cue_index = 1
    for scene in scenes:
        dur = max(float(scene.get('seconds') or 5), 1.0)
        narration = str(scene.get('narration') or scene.get('caption') or '').strip()
        phrases = _subtitle_phrases(narration)
        weights = [max(len(x.replace(' ', '')), 1) for x in phrases]
        total_weight = max(sum(weights), 1)
        local_cursor = cursor
        scene_end = cursor + dur
        for phrase, weight in zip(phrases, weights):
            phrase_dur = dur * weight / total_weight
            end = min(local_cursor + phrase_dur, scene_end)
            chunks.append(
                f'{cue_index}\n{fmt_srt(local_cursor)} --> {fmt_srt(end)}\n{phrase}\n'
            )
            cue_index += 1
            local_cursor = end
        cursor = scene_end
    path.write_text('\n'.join(chunks), encoding='utf-8')
    return path


def make_word_timed_srt(
    audio_parts: list[Path], path: Path, api_key: str, language: str,
    max_chars: int = 16,
) -> Path:
    """Transcribe the final audio and use its word timestamps for captions."""
    client = OpenAI(api_key=api_key)
    cues: list[tuple[float, float, str]] = []
    offset = 0.0
    joiner = '' if language == 'ja' else ' '
    for part in audio_parts:
        with part.open('rb') as audio_file:
            result = client.audio.transcriptions.create(
                model='whisper-1',
                file=audio_file,
                language=language,
                response_format='verbose_json',
                timestamp_granularities=['word'],
            )
        words = list(getattr(result, 'words', None) or [])
        if not words:
            raise RuntimeError(f'No word timestamps returned for {part.name}')
        group: list[str] = []
        start = 0.0
        end = 0.0
        for item in words:
            word = str(getattr(item, 'word', '') or '').strip()
            word_start = float(getattr(item, 'start', 0.0) or 0.0)
            word_end = float(getattr(item, 'end', word_start) or word_start)
            candidate = joiner.join([*group, word]) if word else joiner.join(group)
            if group and (len(candidate.replace(' ', '')) > max_chars or word_end - start > 2.4):
                cues.append((offset + start, offset + end, joiner.join(group)))
                group = []
            if word:
                if not group:
                    start = word_start
                group.append(word)
                end = word_end
        if group:
            cues.append((offset + start, offset + end, joiner.join(group)))
        offset += probe_media_duration(part)
    if not cues:
        raise RuntimeError('Speech transcription produced no subtitle cues')
    chunks = [
        f'{index}\n{fmt_srt(start)} --> {fmt_srt(max(end, start + 0.12))}\n{text}\n'
        for index, (start, end, text) in enumerate(cues, 1)
    ]
    path.write_text('\n'.join(chunks), encoding='utf-8')
    return path


def prepare_short_frames(
    images: list[Path],
    scenes: list[dict[str, Any]],
    out_dir: Path,
    channel_style: str,
    hook: str = '',
    font_path: str | None = None,
) -> list[Path]:
    """Apply the two user-approved master-video layouts before MP4 encoding."""
    if len(images) != len(scenes):
        raise ValueError('Every scene must have exactly one image before layout rendering')
    out_dir.mkdir(parents=True, exist_ok=True)
    title_font = load_font(font_path, 42)
    hook_font = load_font(font_path, 52)
    number_font = load_font(font_path, 30)
    caption_font = load_font(font_path, 46)
    prepared: list[Path] = []
    for index, image_path in enumerate(images, 1):
        with Image.open(image_path).convert('RGB') as source:
            if channel_style == 'ppojjugi_shorts':
                canvas = ImageOps.fit(source, (1080, 1920), method=Image.Resampling.LANCZOS)
                canvas = canvas.filter(ImageFilter.GaussianBlur(28))
                dimmer = Image.new('RGB', canvas.size, '#252733')
                canvas = Image.blend(canvas, dimmer, 0.32)
                panel = ImageOps.fit(source, (1020, 360), method=Image.Resampling.LANCZOS)
                bordered = ImageOps.expand(panel, border=8, fill='#FFFFFF')
                canvas.paste(bordered, ((1080 - bordered.width) // 2, 470))
                draw = ImageDraw.Draw(canvas)
                draw.text((540, 120), '삐죽이의 오늘', font=title_font, fill='#FFFFFF',
                          stroke_width=3, stroke_fill='#383A43', anchor='mm')
                draw.text((540, 1000), f'{index:02d}', font=number_font, fill='#FFFFFF',
                          stroke_width=2, stroke_fill='#383A43', anchor='mm')
            elif channel_style == 'japan_shorts':
                canvas = ImageOps.fit(source, (1080, 1920), method=Image.Resampling.LANCZOS)
                if index == 1 and hook.strip():
                    draw = ImageDraw.Draw(canvas)
                    hook_text = hook.strip()
                    hook_lines: list[str] = []
                    current = ''
                    for ch in hook_text:
                        trial = current + ch
                        box = draw.textbbox((0, 0), trial, font=hook_font)
                        if box[2] - box[0] > 900 and current:
                            hook_lines.append(current)
                            current = ch
                        else:
                            current = trial
                    if current:
                        hook_lines.append(current)
                    hook_text = '\n'.join(hook_lines[:3])
                    draw.multiline_text((540, 150), hook_text, font=hook_font, fill='#FFFFFF',
                                        stroke_width=4, stroke_fill='#30323A', anchor='ma',
                                        align='center', spacing=12)
            else:
                raise ValueError(f'Unknown Shorts master layout: {channel_style}')
            out = out_dir / f'frame_{index:02d}.png'
            canvas.save(out, quality=95)
            prepared.append(out)
    return prepared


def fmt_srt(seconds: float) -> str:
    ms = int((seconds - int(seconds)) * 1000)
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f'{h:02d}:{m:02d}:{s:02d},{ms:03d}'


def compose_short_video(images: list[Path], scenes: list[dict[str, Any]], audio: Path, srt: Path, out_path: Path, channel_style: str, hook: str = '', font_path: str | None = None) -> Path:
    if not shutil.which('ffmpeg'):
        raise RuntimeError('ffmpeg is required for video composition')
    work = out_path.parent / '_segments'
    work.mkdir(parents=True, exist_ok=True)
    frames = prepare_short_frames(images, scenes, work / 'styled_frames', channel_style, hook, font_path)
    segments: list[Path] = []
    for i, (image, scene) in enumerate(zip(frames, scenes), 1):
        dur = max(float(scene.get('seconds') or 5), 1.0)
        seg = work / f'{i:02d}.mp4'
        subprocess.run([
            'ffmpeg', '-y', '-loglevel', 'error', '-loop', '1', '-i', str(image), '-t', str(dur),
            '-vf', 'scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,format=yuv420p',
            '-r', '30', '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(seg),
        ], check=True)
        segments.append(seg)

    concat = work / 'concat.txt'
    concat.write_text(''.join(f"file '{x.resolve().as_posix()}'\n" for x in segments), encoding='utf-8')
    silent = work / 'silent.mp4'
    subprocess.run(['ffmpeg','-y','-loglevel','error','-f','concat','-safe','0','-i',str(concat),'-c','copy',str(silent)], check=True)

    escaped = str(srt.resolve()).replace('\\', '/').replace(':', '\\:').replace("'", "\\'")
    font_name = 'Noto Sans CJK KR' if channel_style == 'ppojjugi_shorts' else 'Noto Sans CJK JP'
    # Both channels use the same low, center-aligned safe-zone. The previous
    # Ppojjugi margin placed captions near the middle of the frame.
    margin_v = 86
    vf = (
        f"subtitles='{escaped}':"
        f"force_style='FontName={font_name},FontSize=14,Bold=1,Outline=2,Shadow=0,"
        f"PrimaryColour=&H00F4F1E8,BackColour=&H70000000,BorderStyle=3,"
        f"Alignment=2,MarginL=120,MarginR=120,MarginV={margin_v}'"
    )
    total_duration = sum(max(float(scene.get('seconds') or 5), 1.0) for scene in scenes)
    audio_filter = (
        'loudnorm=I=-16:TP=-1.5:LRA=11,apad'
        if channel_style == 'ppojjugi_shorts'
        else 'loudnorm=I=-19:TP=-2:LRA=11,apad'
    )
    subprocess.run([
        'ffmpeg','-y','-loglevel','error','-i',str(silent),'-i',str(audio),
        '-vf',vf,'-af',audio_filter,'-t',str(total_duration),
        '-c:v','libx264','-crf','28','-preset','medium','-c:a','aac','-b:a','128k',
        '-movflags','+faststart',str(out_path)
    ], check=True)
    return out_path


def probe_video_streams(path: Path) -> dict[str, Any]:
    if not shutil.which('ffprobe'):
        raise RuntimeError('ffprobe is required for final Shorts verification')
    result = subprocess.run([
        'ffprobe', '-v', 'error', '-show_streams', '-show_format',
        '-of', 'json', str(path),
    ], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def verify_short_artifacts(
    images: list[Path],
    scenes: list[dict[str, Any]],
    audio: Path,
    srt: Path,
    video: Path,
    strict_caption_count: bool = True,
) -> dict[str, Any]:
    """Verify the final bytes that will be attached/uploaded, not just the plan."""
    if len(images) != len(scenes) or not scenes:
        raise RuntimeError('Shorts verification requires one image for every scene')
    required = [*images, audio, srt, video]
    if any(not path.is_file() or path.stat().st_size <= 0 for path in required):
        raise RuntimeError('Shorts verification found a missing or empty artifact')
    expected_captions = sum(
        len(_subtitle_phrases(str(scene.get('narration') or scene.get('caption') or '')))
        for scene in scenes
    )
    observed_captions = sum(
        bool(line.strip().isdigit())
        for line in srt.read_text(encoding='utf-8').splitlines()
    )
    if strict_caption_count and observed_captions != expected_captions:
        raise RuntimeError('Shorts subtitle cue count does not match the spoken narration phrases')
    if observed_captions < 1:
        raise RuntimeError('Shorts subtitle file contains no timed cues')
    info = probe_video_streams(video)
    streams = info.get('streams') or []
    video_streams = [stream for stream in streams if stream.get('codec_type') == 'video']
    audio_streams = [stream for stream in streams if stream.get('codec_type') == 'audio']
    if len(video_streams) != 1 or not audio_streams:
        raise RuntimeError('Shorts MP4 must contain one video stream and an audio stream')
    stream = video_streams[0]
    if (int(stream.get('width') or 0), int(stream.get('height') or 0)) != (1080, 1920):
        raise RuntimeError('Shorts MP4 must be exactly 1080x1920')
    expected_duration = sum(max(float(scene.get('seconds') or 0), 1.0) for scene in scenes)
    actual_duration = float((info.get('format') or {}).get('duration') or 0)
    tolerance = max(0.75, expected_duration * 0.02)
    if actual_duration <= 0 or abs(actual_duration - expected_duration) > tolerance:
        raise RuntimeError('Shorts MP4 duration does not match the actual scene audio timeline')
    return {
        'pass': True,
        'image_count': len(images),
        'subtitle_cues': observed_captions,
        'duration_seconds': round(actual_duration, 3),
        'width': 1080,
        'height': 1920,
        'audio_streams': len(audio_streams),
    }


def save_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
