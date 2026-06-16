from pathlib import Path
import re

VIDEO_DIR = Path(r"F:\Goyamart series")  # Change to your folder path

for video_file in VIDEO_DIR.glob("*.mp4"):
    old_name = video_file.stem

    # Extract episode number
    match = re.search(r"(?:Series?|Սերիա)\s*(\d+)", old_name, re.IGNORECASE)

    if match:
        episode_no = match.group(1)
        new_name = f"Goyamart Episode {episode_no}{video_file.suffix}"

        new_path = video_file.with_name(new_name)

        # Avoid overwriting existing files
        if not new_path.exists():
            video_file.rename(new_path)
            print(f"Renamed: {video_file.name} -> {new_name}")
        else:
            print(f"Skipped (already exists): {new_name}")
    else:
        print(f"Episode number not found: {video_file.name}")

print("Done.")