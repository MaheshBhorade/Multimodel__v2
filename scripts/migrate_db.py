import os
import shutil
import sys
from pathlib import Path

# Add src to sys.path to allow imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

def main():
    print("Stopping current databases and indexes...")
    # Delete database files
    for db_file in ["content_platform.db", "platform_library.db"]:
        p = Path(db_file)
        if p.exists():
            try:
                os.remove(p)
                print(f"Deleted {db_file}")
            except Exception as e:
                print(f"Could not delete {db_file}: {e}")
                
    # Delete FAISS indexes
    faiss_dir = Path("runtime/faiss")
    if faiss_dir.exists():
        try:
            shutil.rmtree(faiss_dir)
            print("Deleted runtime/faiss/")
        except Exception as e:
            print(f"Could not delete runtime/faiss/: {e}")
    faiss_dir.mkdir(parents=True, exist_ok=True)

    # Initialize new SQLite databases
    from content_platform.server.db import engine, platform_engine, Base, PlatformBase
    Base.metadata.create_all(bind=engine)
    PlatformBase.metadata.create_all(bind=platform_engine)
    print("Recreated database tables with the new schemas.")

    # Create the reference library directory structure
    ref_lib = Path("reference_library")
    ref_lib.mkdir(parents=True, exist_ok=True)

    platforms_dir = ref_lib / "platforms"
    movies_dir = ref_lib / "movies"
    series_dir = ref_lib / "series"

    platforms_dir.mkdir(parents=True, exist_ok=True)
    movies_dir.mkdir(parents=True, exist_ok=True)
    series_dir.mkdir(parents=True, exist_ok=True)

    # Reorganize/copy files if they exist
    # 1. Platforms
    youtube_dest = platforms_dir / "youtube"
    youtube_dest.mkdir(parents=True, exist_ok=True)
    youtube_src = Path("platform/videos/Youtube.mp4")
    if youtube_src.exists():
        shutil.copy2(youtube_src, youtube_dest / "Youtube.mp4")
        print(f"Copied platform YouTube layout to {youtube_dest}")

    prime_dest = platforms_dir / "prime_video"
    prime_dest.mkdir(parents=True, exist_ok=True)
    uplay_src = Path("platform/videos/Uplay.mp4")
    if uplay_src.exists():
        shutil.copy2(uplay_src, prime_dest / "Uplay.mp4")
        print(f"Copied platform Uplay layout to {prime_dest}")

    # 2. Movies (Music Videos/Songs treated as movies in the new schema)
    bangles_dest = movies_dir / "bangles"
    bangles_dest.mkdir(parents=True, exist_ok=True)
    bangles_src = Path("videos/bangles.mp4")
    if bangles_src.exists():
        shutil.copy2(bangles_src, bangles_dest / "bangles.mp4")
        print(f"Copied bangles video to {bangles_dest}")

    shararat_dest = movies_dir / "shararat"
    shararat_dest.mkdir(parents=True, exist_ok=True)
    shararat_src = Path("videos/shararat.mp4")
    if shararat_src.exists():
        shutil.copy2(shararat_src, shararat_dest / "shararat.mp4")
        print(f"Copied shararat video to {shararat_dest}")

    print("\nMigration setup successfully completed!")

if __name__ == "__main__":
    main()
