from pathlib import Path
import cv2

VIDEO_DIR = Path(r"D:\Multimodel_Fingerprint\platform\videos")
FRAME_DIR = Path(r"D:\Multimodel_Fingerprint\platform\frames")

SAVE_EVERY_N_SECONDS = 1

VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv"}


def extract_frames(video_path: Path):
    cap = cv2.VideoCapture(str(video_path))

    if not cap.isOpened():
        print(f"[ERROR] Failed to open: {video_path}")
        return

    fps = cap.get(cv2.CAP_PROP_FPS)

    if fps <= 0:
        print(f"[ERROR] Invalid FPS for {video_path}")
        cap.release()
        return

    frame_interval = max(1, int(fps * SAVE_EVERY_N_SECONDS))

    output_folder = FRAME_DIR / video_path.stem
    output_folder.mkdir(parents=True, exist_ok=True)

    frame_count = 0
    saved_count = 0

    while True:
        ret, frame = cap.read()

        if not ret:
            break

        if frame_count % frame_interval == 0:
            output_path = output_folder / f"frame_{saved_count:06d}.jpg"

            success = cv2.imwrite(
                str(output_path),
                frame,
                [cv2.IMWRITE_JPEG_QUALITY, 95]
            )

            if success:
                saved_count += 1

        frame_count += 1

    cap.release()

    print(
        f"[DONE] {video_path.name} | "
        f"Frames: {frame_count} | "
        f"Saved: {saved_count}"
    )


def main():
    FRAME_DIR.mkdir(parents=True, exist_ok=True)

    videos = [
        f for f in VIDEO_DIR.iterdir()
        if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS
    ]

    print(f"Found {len(videos)} video(s)")

    for video in videos:
        extract_frames(video)

    print("Completed")


if __name__ == "__main__":
    main()