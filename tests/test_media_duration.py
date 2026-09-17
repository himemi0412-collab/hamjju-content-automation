from pathlib import Path

from app.media import compose_short_video


def test_compose_short_video_pads_audio_to_full_scene_duration(monkeypatch, tmp_path):
    monkeypatch.setattr('app.media.shutil.which', lambda _: 'ffmpeg')
    calls = []

    def record_call(args, check):
        calls.append(args)

    monkeypatch.setattr('app.media.subprocess.run', record_call)
    monkeypatch.setattr('app.media.prepare_short_frames', lambda images, scenes, *_args, **_kwargs: images)
    images = [tmp_path / 'one.png', tmp_path / 'two.png']
    scenes = [
        {'seconds': 5, 'caption': 'one'},
        {'seconds': 7, 'caption': 'two'},
    ]
    audio = tmp_path / 'voice.mp3'
    srt = tmp_path / 'captions.srt'
    out = tmp_path / 'short.mp4'

    result = compose_short_video(images, scenes, audio, srt, out, channel_style='japan_shorts')

    final_call = calls[-1]
    assert result == out
    assert '-shortest' not in final_call
    assert final_call[final_call.index('-af') + 1] == 'apad'
    assert final_call[final_call.index('-t') + 1] == '12.0'
