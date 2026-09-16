from __future__ import annotations
import base64
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any
from PIL import Image, ImageDraw, ImageFont
from openai import OpenAI

PALETTE = [
    '#F7F8FB', '#E7F1FF', '#EDE9FE', '#FFE8EF', '#DFF7EE',
    '#E8F5F7', '#F0EAF8', '#F8EAF2', '#E6F2EC', '#E9EEF7',
    '#F3E8EE', '#E4F0F2', '#ECE8F5', '#F7E9E6', '#E2EFE7',
    '#E8ECF4', '#F1E7F0', '#E5F1EE', '#EEEAF3', '#E7EDF2',
    '#F5E8EC', '#E3EFF5', '#EBE7F2', '#E6F3F0',
]
TEXT = '#20242C'
ACCENT = '#5B67D8'


class MediaGenerator:
    def __init__(self, api_key: str, image_model: str, tts_model: str, voice: str, font_path: str | None = None):
        self.client = OpenAI(api_key=api_key)
        self.image_model = image_model
        self.tts_model = tts_model
        self.voice = voice
        self.font_path = font_path

    def generate_scene_images(self, scenes: list[dict[str, Any]], out_dir: Path) -> list[Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        for i, scene in enumerate(scenes, 1):
            prompt = str(scene.get('image_prompt') or scene.get('caption') or '')
            response = self.client.images.generate(
                model=self.image_model,
                prompt=prompt + '\nVertical composition. Clean natural color. No yellow cast. No sepia filter.',
                size='1024x1536',
            )
            item = response.data[0]
            b64 = getattr(item, 'b64_json', None)
            if not b64:
                raise RuntimeError('Image API returned no base64 image data')
            path = out_dir / f'scene_{i:02d}.png'
            path.write_bytes(base64.b64decode(b64))
            paths.append(path)
        return paths

    def generate_tts(self, text: str, out_path: Path) -> Path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with self.client.audio.speech.with_streaming_response.create(
            model=self.tts_model,
            voice=self.voice,
            input=text,
        ) as response:
            response.stream_to_file(out_path)
        return out_path


def render_blog_cards(cards: list[dict[str, Any]], out_dir: Path, font_path: str | None = None) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cards = cards[:5]
    if not cards:
        return []
    font_big = load_font(font_path, 64)
    font_body = load_font(font_path, 38)
    paths: list[Path] = []
    for i, card in enumerate(cards, 1):
        seed = sum(ord(ch) for ch in str(card.get('headline', '')))\n        bg = PALETTE[(seed + i * 7) % len(PALETTE)]
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


def fmt_srt(seconds: float) -> str:
    ms = int((seconds - int(seconds)) * 1000)
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f'{h:02d}:{m:02d}:{s:02d},{ms:03d}'


def compose_short_video(images: list[Path], scenes: list[dict[str, Any]], audio: Path, srt: Path, out_path: Path) -> Path:
    if not shutil.which('ffmpeg'):
        raise RuntimeError('ffmpeg is required for video composition')
    work = out_path.parent / '_segments'
    work.mkdir(parents=True, exist_ok=True)
    segments: list[Path] = []
    for i, (image, scene) in enumerate(zip(images, scenes), 1):
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
    vf = f"subtitles='{escaped}':force_style='FontSize=18,Outline=2,Shadow=0,Alignment=2,MarginV=90'"
    subprocess.run([
        'ffmpeg','-y','-loglevel','error','-i',str(silent),'-i',str(audio),
        '-vf',vf,'-c:v','libx264','-crf','28','-preset','medium','-c:a','aac','-b:a','128k','-shortest','-movflags','+faststart',str(out_path)
    ], check=True)
    return out_path


def save_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
