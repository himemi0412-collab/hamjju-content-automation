from pathlib import Path

import pytest

from app.media import compose_short_video, verify_short_artifacts


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
    audio_filter = final_call[final_call.index('-af') + 1]
    assert audio_filter.endswith(',apad') or audio_filter == 'apad'
    assert 'loudnorm=' in audio_filter
    assert final_call[final_call.index('-t') + 1] == '12.0'


def test_final_short_verification_checks_scene_subtitle_audio_and_dimensions(monkeypatch, tmp_path):
    images = [tmp_path / 'one.png', tmp_path / 'two.png']
    audio = tmp_path / 'voice.mp3'
    srt = tmp_path / 'captions.srt'
    video = tmp_path / 'short.mp4'
    for path in [*images, audio, video]:
        path.write_bytes(b'asset')
    srt.write_text(
        '1\n00:00:00,000 --> 00:00:05,000\none\n\n'
        '2\n00:00:05,000 --> 00:00:12,000\ntwo\n',
        encoding='utf-8',
    )
    scenes = [
        {'seconds': 5, 'caption': 'one'},
        {'seconds': 7, 'caption': 'two'},
    ]
    monkeypatch.setattr('app.media.probe_video_streams', lambda _: {
        'streams': [
            {'codec_type': 'video', 'width': 1080, 'height': 1920},
            {'codec_type': 'audio'},
        ],
        'format': {'duration': '12.0'},
    })

    result = verify_short_artifacts(images, scenes, audio, srt, video)

    assert result == {
        'pass': True,
        'image_count': 2,
        'subtitle_cues': 2,
        'duration_seconds': 12.0,
        'width': 1080,
        'height': 1920,
        'audio_streams': 1,
    }


def test_final_short_verification_rejects_missing_audio_stream(monkeypatch, tmp_path):
    image = tmp_path / 'one.png'
    audio = tmp_path / 'voice.mp3'
    srt = tmp_path / 'captions.srt'
    video = tmp_path / 'short.mp4'
    for path in [image, audio, video]:
        path.write_bytes(b'asset')
    srt.write_text('1\n00:00:00,000 --> 00:00:05,000\none\n', encoding='utf-8')
    monkeypatch.setattr('app.media.probe_video_streams', lambda _: {
        'streams': [{'codec_type': 'video', 'width': 1080, 'height': 1920}],
        'format': {'duration': '5.0'},
    })

    with pytest.raises(RuntimeError, match='audio stream'):
        verify_short_artifacts([image], [{'seconds': 5, 'caption': 'one'}], audio, srt, video)
