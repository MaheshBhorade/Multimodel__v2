import cv2
import numpy as np
from pathlib import Path

LOGO_FP_DIR = Path(r"D:\Multimodel_Fingerprint\logo_fp\PanArmenian TV")

img_files = list(LOGO_FP_DIR.glob("*.jpg"))
print(f"Found {len(img_files)} images.")

for img_path in sorted(img_files)[:20]:
    frame = cv2.imread(str(img_path))
    if frame is None:
        continue
    h, w = frame.shape[:2]
    ymin, ymax = int(h * 0.75), int(h * 0.95)
    xmin, xmax = int(w * 0.75), int(w * 0.98)
    logo_roi = frame[ymin:ymax, xmin:xmax]
    std = np.std(logo_roi)
    mean = np.mean(logo_roi)
    print(f"Image: {img_path.name} | ROI Std: {std:.2f} | ROI Mean: {mean:.2f}")
