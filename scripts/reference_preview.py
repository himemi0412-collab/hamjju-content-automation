from pathlib import Path
import os

from app.media import MediaGenerator
from app.settings import Settings


def main() -> None:
    settings = Settings()
    previews = [
        (
            'ppojjugi_shorts',
            settings.tts_ppojjugi_voice,
            '오늘도 단기 알바를 끝냈다. 열심히 일했는데, 집에 가는 길에 갑자기 현타가 왔다.',
            'Young adult Korean woman speaking in a soft, dry, intimate diary monologue. Low-energy and slightly weary, with restrained emotion, gentle downward sentence endings, small natural sighs, clear but unforced Korean, and about half a second of space between thoughts. No bright smile, cute acting, announcer voice, advertising rhythm, or melodrama.',
            'A new single landscape 3:2 scene: the exact same golden hamster character from the reference, lavender top and dark green apron, walking home after a short-term job through a muted evening city street. Tired, blank, inward-looking expression. Match the reference character drawing, painterly urban background density, subdued palette, cinematic side composition, thin hand-drawn line, and soft 2D shading. Complete character visible. No words, numbers, logos, collage, photorealism, 3D, yellow cast, or sepia.',
        ),
        (
            'japan_shorts',
            settings.tts_japan_voice,
            '壁には、好きな人の写真が何枚も貼られていました。あの頃の部屋には、夢を見る場所がありました。',
            'Mature Japanese man with low chest resonance and a quiet documentary storytelling tone. Natural standard Japanese at a measured pace, restrained nostalgia, softly falling sentence endings, and short pauses of about half a second between clauses. No youthful brightness, anime acting, commercial narration, exaggerated old-man acting, or sentimental overperformance.',
            'A new single vertical scene set in a modest 1980s Japanese room: a young woman quietly looking at magazine clippings and a record on a wooden desk. Match the reference exactly in realistic hand-drawn watercolor and colored-pencil treatment, mature restrained anatomy, fine graphite outlines, paper grain, muted cool whites, gray-blue, faded green, and soft brown, documentary composition, calm everyday realism. No words, logos, collage, photorealism, glossy anime look, yellow cast, gold, or sepia.',
        ),
    ]
    for channel, voice, text, instructions, prompt in previews:
        out = settings.output_dir / 'reference_preview' / channel
        media = MediaGenerator(
            settings.openai_api_key,
            settings.image_model,
            settings.tts_model,
            voice,
            settings.card_font_path,
            settings.image_quality,
        )
        if os.getenv('VOICE_ONLY') != '1':
            media.generate_scene_images([{'image_prompt': prompt, 'caption': ''}], out / 'images', channel)
        media.generate_tts(text, out / 'voice.mp3', instructions=instructions)


if __name__ == '__main__':
    main()
