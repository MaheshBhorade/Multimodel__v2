import json
import uuid
from pathlib import Path

import cv2
import librosa

from content_platform.server.db import (
    Base,
    engine,
    SessionLocal,
)
from content_platform.server.models import (
    ContentLibrary,
)
from content_platform.fingerprint.unified import (
    UnifiedFingerprinter,
)

SEGMENT_SECONDS = 10


def audio_to_string(fp: list[float]) -> str:
    return "ae-hash-" + "-".join(
        f"{x:.6f}" for x in fp
    )


def ingest_video(
    video_path: str,
    title: str,
    category: str = "song",
):

    db = SessionLocal()

    try:

        content_id = str(uuid.uuid4())

        print("\n" + "=" * 80)
        print(f"PROCESSING : {title}")
        print(f"FILE       : {video_path}")
        print("=" * 80)

        cap = cv2.VideoCapture(video_path)

        print("OPENED =", cap.isOpened())

        if not cap.isOpened():
            raise RuntimeError(
                f"Cannot open video: {video_path}"
            )

        fps = cap.get(
            cv2.CAP_PROP_FPS
        )

        frame_count = int(
            cap.get(
                cv2.CAP_PROP_FRAME_COUNT
            )
        )

        print("FPS         =", fps)
        print("FRAME_COUNT =", frame_count)

        if fps <= 0:
            raise RuntimeError(
                f"Invalid FPS: {fps}"
            )

        duration = int(
            frame_count / fps
        )

        print(
            f"DURATION = {duration}s"
        )

        print("Loading audio...")

        audio, sr = librosa.load(
            video_path,
            sr=16000,
            mono=True
        )

        print(
            f"Audio loaded. Samples={len(audio)} SR={sr}"
        )

        inserted = 0

        for sec in range(
            0,
            duration,
            SEGMENT_SECONDS
        ):

            frame_number = int(
                sec * fps
            )

            cap.set(
                cv2.CAP_PROP_POS_FRAMES,
                frame_number
            )

            ret, frame = cap.read()

            if not ret:
                print(
                    f"Skipping {sec}s"
                )
                continue

            visual_fp = (
                UnifiedFingerprinter
                .visual_fingerprint(frame)
            )

            print(
                f"VISUAL SAMPLE @{sec}s:",
                visual_fp[:5]
            )

            start_audio = sec * sr

            end_audio = min(
                len(audio),
                (sec + SEGMENT_SECONDS) * sr
            )

            audio_chunk = audio[
                start_audio:end_audio
            ]

            audio_fp = (
                UnifiedFingerprinter
                .audio_fingerprint(
                    audio_chunk,
                    sr
                )
            )

            print(
                f"AUDIO SAMPLE @{sec}s:",
                audio_fp[:5]
            )

            db.add(
                ContentLibrary(
                    external_content_id=content_id,
                    title=title,
                    category=category,
                    channel_name=None,
                    segment_offset=sec,
                    visual_fp=json.dumps(
                        visual_fp
                    ),
                    audio_fp=json.dumps(
                        audio_fp
                    ),
                    logo_fp="[]",
                    ocr_keywords=""
                )
            )

            inserted += 1

            print(
                f"[{inserted}] Stored segment {sec}s"
            )

        print(
            f"Committing {inserted} segments..."
        )

        db.commit()

        print(
            f"SUCCESS: {title}"
        )

    except Exception as e:

        db.rollback()

        print(
            f"FAILED: {title}"
        )

        raise e

    finally:

        try:
            cap.release()
        except Exception:
            pass

        db.close()


if __name__ == "__main__":

    print(
        "\nResetting database tables..."
    )

    Base.metadata.drop_all(
        bind=engine
    )

    Base.metadata.create_all(
        bind=engine
    )

    print(
        "Database ready."
    )

    reference_dir = (
        Path("manual_ingestion")
        / "reference_library"
    )

    print(
        f"Reference Directory = {reference_dir}"
    )

    if not reference_dir.exists():
        raise RuntimeError(
            f"Directory not found: {reference_dir}"
        )

    videos = list(
        reference_dir.glob("*.mp4")
    )

    if not videos:
        raise RuntimeError(
            "No MP4 files found."
        )

    print(
        f"Found {len(videos)} videos"
    )

    for video in videos:

        ingest_video(
            str(video),
            video.stem,
            "song"
        )

    print(
        "\nALL VIDEOS PROCESSED"
    )