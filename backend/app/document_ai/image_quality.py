"""OpenCV-based image quality signals used for scanned pages and image uploads."""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

# Heuristic thresholds (tunable). Laplacian variance below BLUR_THRESHOLD => likely blurry.
BLUR_THRESHOLD = 100.0
LOW_CONTRAST_THRESHOLD = 25.0
MIN_DPI_EQUIVALENT_WIDTH = 1000  # px width of an A4 page at ~120 dpi


def to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img
    if img.shape[2] == 4:
        return cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def assess_image_quality(img: np.ndarray) -> dict[str, Any]:
    gray = to_gray(img)
    h, w = gray.shape[:2]
    # Normalise scale so the blur score is comparable across resolutions.
    scale = 1000.0 / max(w, 1)
    norm = cv2.resize(gray, (1000, max(1, int(h * scale)))) if w > 1000 else gray
    blur_score = float(cv2.Laplacian(norm, cv2.CV_64F).var())
    contrast = float(gray.std())
    issues: list[str] = []
    if blur_score < BLUR_THRESHOLD:
        issues.append("BLURRY")
    if contrast < LOW_CONTRAST_THRESHOLD:
        issues.append("LOW_CONTRAST")
    if w < MIN_DPI_EQUIVALENT_WIDTH:
        issues.append("LOW_RESOLUTION")
    # Quality score in [0,1]: crude blend of sharpness/contrast/resolution.
    score = (
        0.5 * min(1.0, blur_score / (BLUR_THRESHOLD * 3))
        + 0.3 * min(1.0, contrast / 60.0)
        + 0.2 * min(1.0, w / 1600.0)
    )
    return {
        "width_px": int(w),
        "height_px": int(h),
        "blur_score": round(blur_score, 2),
        "contrast": round(contrast, 2),
        "quality_score": round(score, 3),
        "issues": issues,
    }


MIN_OCR_WIDTH = 1600  # upscale smaller images: Tesseract accuracy drops sharply below ~200 dpi


def preprocess_for_ocr(img: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Grayscale -> upscale (if small) -> denoise -> adaptive threshold -> deskew.

    Returns (binarised image, 2x3 affine matrix mapping ORIGINAL pixel coords -> processed coords).
    OCR boxes found on the processed image are mapped back with `map_box_to_original`, so
    provenance always refers to the original page.
    """
    gray = to_gray(img)
    h, w = gray.shape[:2]
    scale = MIN_OCR_WIDTH / w if w < MIN_OCR_WIDTH else 1.0
    if scale != 1.0:
        gray = cv2.resize(gray, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)
    gray = cv2.fastNlMeansDenoising(gray, h=10)
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15)
    m_scale = np.array([[scale, 0, 0], [0, scale, 0]], dtype=np.float64)
    rotated, m_rot = deskew(binary)
    # compose: original -> scaled -> rotated
    m_total = m_rot @ np.vstack([m_scale, [0, 0, 1]])
    return rotated, m_total


def estimate_skew(binary: np.ndarray) -> float:
    coords = np.column_stack(np.where(binary < 128))
    if coords.shape[0] < 50:
        return 0.0
    angle = cv2.minAreaRect(coords[:, ::-1].astype(np.float32))[-1]
    if angle > 45:
        angle -= 90
    elif angle < -45:
        angle += 90
    return float(angle)


def deskew(binary: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Rotate small skews (0.3-15 deg). Returns (image, 2x3 rotation matrix)."""
    h, w = binary.shape[:2]
    identity = np.array([[1, 0, 0], [0, 1, 0]], dtype=np.float64)
    angle = estimate_skew(binary)
    if abs(angle) < 0.3 or abs(angle) > 15:
        return binary, identity
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    out = cv2.warpAffine(binary, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    return out, m


def map_box_to_original(box: tuple[float, float, float, float], m: np.ndarray) -> tuple[float, float, float, float]:
    """Map an axis-aligned box from processed-image coords back to original-image coords."""
    inv = cv2.invertAffineTransform(m)
    x0, y0, x1, y1 = box
    pts = np.array([[x0, y0], [x1, y0], [x0, y1], [x1, y1]], dtype=np.float64)
    mapped = pts @ inv[:, :2].T + inv[:, 2]
    return (float(mapped[:, 0].min()), float(mapped[:, 1].min()),
            float(mapped[:, 0].max()), float(mapped[:, 1].max()))


def decode_image_file(path: str) -> np.ndarray | None:
    data = np.fromfile(path, dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)
