from pathlib import Path
import shutil

ROOT_DIR = Path(r"D:\Multimodel_Fingerprint\platform\frames")  # Change this path
OUTPUT_DIR = ROOT_DIR / "frames"

OUTPUT_DIR.mkdir(exist_ok=True)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

counter = 1

for folder in ROOT_DIR.rglob("*"):
    if not folder.is_dir() or folder == OUTPUT_DIR:
        continue

    for img in folder.iterdir():
        if img.is_file() and img.suffix.lower() in IMAGE_EXTENSIONS:

            new_name = f"img_{counter:08d}{img.suffix.lower()}"
            destination = OUTPUT_DIR / new_name

            shutil.copy2(img, destination)   # Copy image
            # shutil.move(img, destination)  # Uncomment if you want to MOVE instead

            counter += 1

print(f"Done! {counter-1} images saved to:")
print(OUTPUT_DIR)