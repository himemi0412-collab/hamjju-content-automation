from __future__ import annotations
import base64
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
from openai import OpenAI

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
        with self.client.audio.speech.with_streaming_response.create(
            model=self.tts_model,
            voice=self.voice,
            input=text,
            instructions=instructions,
        ) as response:
            response.stream_to_file(out_path)
        return out_path


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


def render_blog_cards(cards: list[dict[str, Any]], out_dir: Path, font_path: str | None = None) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cards = cards[:5]
    if not cards:
        return []
    font_big = load_font(font_path, 64)
    font_body = load_font(font_path, 38)
    paths: list[Path] = []
    for i, card in enumerate(cards, 1):
        seed = sum(ord(ch) for ch in str(card.get('headline', '')))
        bg = PALETTE[(seed + i * 7) % len(PALETTE)]
        im = Image.new('RGB', (1080, 1350), bg)
        draw = ImageDraw.Draw(im)
        draw.rounded_rectangle((70, 90, 1010, 1260), radius=44, fill='#FFFFFF')
        draw.rounded_rectangle((90, 115, 230, 185), radius=28, fill=ACCENT)
        draw.text((160, 150), f'{i}/5', font=font_body, fill='#FFFFFF', anchor='mm')
        draw_multiline(draw, str(card.get('headline', '')), (110, 245), 820, font_big, TEXT, spacing=16)
        draw_multiline(draw, str(card.get('copy', '')), (110, 560), 840, font_body, TEXT, spacing=14)
        path = out_dir / f'card_{i:02d}.png'
        im.save(path, quality=95)
        paths.append(path)
    return paths


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
