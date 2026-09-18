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

PALETTE = [
    '#F7F8FB', '#E7F1FF', '#EDE9FE', '#FFE8EF', '#DFF7EE',
    '#E8F5F7', '#F0EAF8', '#F8EAF2', '#E6F2EC', '#E9EEF7',
    '#F3E8EE', '#E4F0F2', '#ECE8F5', '#F7E9E6', '#E2EFE7',
    '#E8ECF4', '#F1E7F0', '#E5F1EE', '#EEEAF3', '#E7EDF2',
    '#F5E8EC', '#E3EFF5', '#EBE7F2', '#E6F3F0',
]
TEXT = '#20242C'
ACCENT = '#5B67D8'

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
            self._generate_fal_tts(text, out_path, instructions)
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
    palette = family_palettes[visual_family]
    primary, secondary, tertiary, ink = palette
    for i, card in enumerate(cards, 1):
        layout = card.get('layout')
        if layout != layouts[i - 1] or card.get('card') != i:
            raise ValueError(f'Card {i} must use layout={layouts[i - 1]} and matching card number')
        headline = _blog_required_text(card.get('headline'), f'card {i} headline')
        copy = _blog_required_text(card.get('copy'), f'card {i} copy')
        illustration = card.get('illustration')
        # A model may occasionally repeat one of these visual-family tokens in
        # the illustration slot. Normalize only the two unambiguous cases to a
        # neutral explanatory symbol; neither mapping adds product or factual
        # detail. Ambiguous family tokens remain fail-closed.
        illustration = {
            'playful_diagram': 'diagram',
            'clipboard_checklist': 'document',
        }.get(illustration, illustration)
        if illustration not in {
            'bubbles', 'laundry', 'wifi', 'document', 'appliance',
            'measurement', 'home', 'diagram', 'garment', 'steamer',
        }:
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
        im = Image.new('RGB', (width, height), '#FFFFFF')
        draw = ImageDraw.Draw(im)
        _blog_family_decor(draw, visual_family, width, height, primary, secondary, tertiary, ink)
        role = ('먼저 답', '왜 그런지', '같은 조건 비교', '지금 확인', '마지막 판단')[i - 1]
        draw.rounded_rectangle(box((62, 48, 390, 102)), radius=size(20), fill=primary)
        _blog_text(draw, role, box((84, 60, 370, 92)), font_path, size(25), ink, 1)
        _blog_text(draw, f'{i:02d} / 05', box((868, 60, 1018, 92)), font_path, size(25), ink, 1)
        if visual_family == 'contract_notebook':
            draw.rounded_rectangle(box((54, 128, 1026, 300)), radius=size(18), fill='#F2EAF6')
        _blog_text(draw, headline, box((62, 140, 1018, 292)), font_path, size(64), ink, 2, title=True)
        _blog_text(draw, copy, box((64, 306, 1016, 396)), font_path, size(33), ink, 2)

        if layout == 'cover':
            draw.ellipse(box((312, 418, 768, 822)), fill=primary)
            _blog_symbol(draw, illustration, point((540, 610)), size(245), ink, '#FFFFFF')
            for j, item in enumerate(items):
                x = 64 + j * 500
                draw.rounded_rectangle(box((x, 824, x + 452, 984)), radius=size(24), fill=(secondary, tertiary)[j])
                _blog_text(draw, item['label'], box((x + 24, 846, x + 428, 892)), font_path, size(34), ink, 1, title=True)
                _blog_text(draw, item['detail'], box((x + 24, 907, x + 428, 970)), font_path, size(27), ink, 2)
        elif layout == 'flow':
            for j, item in enumerate(items):
                y = 438 + j * 181
                color = (primary, secondary, tertiary)[j]
                draw.ellipse(box((66, y + 15, 164, y + 113)), fill=color)
                _blog_text(draw, str(j + 1), box((99, y + 34, 143, y + 92)), font_path, size(43), ink, 1, title=True)
                draw.line(box((196, y + 18, 196, y + 132)), fill=color, width=size(8))
                _blog_text(draw, item['label'], box((224, y + 2, 998, y + 53)), font_path, size(39), ink, 1, title=True)
                _blog_text(draw, item['detail'], box((224, y + 67, 998, y + 144)), font_path, size(30), ink, 2)
                if j < 2:
                    _blog_arrow(draw, point((115, y + 132)), point((115, y + 169)), ink)
        elif layout == 'comparison':
            for j, item in enumerate(items):
                x = 64 + j * 500
                color = (primary, secondary)[j]
                draw.rounded_rectangle(box((x, 436, x + 452, 970)), radius=size(28), fill=color)
                _blog_text(draw, item['label'], box((x + 26, 466, x + 426, 570)), font_path, size(42), ink, 2, title=True)
                # Two columns share the same visual and scale: no invented
                # better/worse score or unverified product appearance.
                _blog_symbol(draw, illustration, point((x + 226, 651)), size(117), ink, '#FFFFFF')
                _blog_text(draw, item['detail'], box((x + 30, 749, x + 422, 944)), font_path, size(31), ink, 5)
        elif layout == 'checklist':
            item_height = 132 if len(items) == 4 else 173
            for j, item in enumerate(items):
                y = 426 + j * item_height
                draw.rounded_rectangle(box((64, y + 9, 133, y + 78)), radius=size(16), fill=primary)
                draw.line([point((83, y + 40)), point((98, y + 55)), point((117, y + 30))], fill=ink, width=size(6))
                _blog_text(draw, item['label'], box((164, y + 4, 1005, y + 48)), font_path, size(34), ink, 1, title=True)
                _blog_text(draw, item['detail'], box((164, y + 59, 1005, y + item_height - 5)), font_path, size(28), ink, 2)
                if j < len(items) - 1:
                    draw.line(box((165, y + item_height - 4, 1007, y + item_height - 4)), fill=tertiary, width=size(2))
        else:  # decision: three labeled stops on a reading path.
            draw.line(box((107, 486, 107, 883)), fill=primary, width=size(12))
            for j, item in enumerate(items):
                y = 429 + j * 180
                draw.ellipse(box((76, y + 20, 138, y + 82)), fill=(primary, secondary, tertiary)[j], outline=ink, width=size(3))
                _blog_text(draw, str(j + 1), box((95, y + 30, 127, y + 71)), font_path, size(29), ink, 1)
                _blog_text(draw, item['label'], box((178, y + 1, 1009, y + 54)), font_path, size(40), ink, 1, title=True)
                _blog_text(draw, item['detail'], box((178, y + 71, 1009, y + 144)), font_path, size(30), ink, 2)
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


def make_srt(scenes: list[dict[str, Any]], path: Path) -> Path:
    cursor = 0.0
    chunks = []
    for i, scene in enumerate(scenes, 1):
        dur = max(float(scene.get('seconds') or 5), 1.0)
        start, end = cursor, cursor + dur
        caption = str(scene.get('caption') or '').strip()
        if caption:
            chunks.append(f'{i}\n{fmt_srt(start)} --> {fmt_srt(end)}\n{caption}\n')
        cursor = end
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
                caption_text = str(scenes[index - 1].get('caption') or '').strip()
                if caption_text:
                    caption_lines: list[str] = []
                    current = ''
                    for ch in caption_text:
                        trial = current + ch
                        box = draw.textbbox((0, 0), trial, font=caption_font)
                        if box[2] - box[0] > 850 and current:
                            caption_lines.append(current)
                            current = ch
                        else:
                            current = trial
                    if current:
                        caption_lines.append(current)
                    draw.multiline_text(
                        (540, 875), '\n'.join(caption_lines[:2]), font=caption_font,
                        fill='#FFFFFF', stroke_width=3, stroke_fill='#383A43',
                        anchor='ma', align='center', spacing=10,
                    )
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
    if channel_style == 'ppojjugi_shorts':
        vf = 'null'
    else:
        vf = (
            f"subtitles='{escaped}':"
            f"force_style='FontName=Noto Sans CJK JP,FontSize=18,Outline=2,Shadow=0,"
            f"Alignment=2,MarginV=110'"
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


def save_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
