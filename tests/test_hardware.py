from content_platform.edge.hardware import discover_audio_device, discover_video_device


def test_discovery_functions_return_supported_shapes() -> None:
    audio = discover_audio_device()
    video = discover_video_device()

    assert isinstance(audio, str)
    assert video is None or video.startswith("/dev/video")
