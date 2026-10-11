import colorsys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union
import numpy as np
from PIL import Image


@dataclass
class ImageColorResult:
    """Outcome of analyzing dominant color from an image."""
    status: str  # 'EXTRACTED', 'UNCERTAIN', 'BACKGROUND_ONLY', 'FAILED'
    canonical_color: Optional[str] = None  # Standardized color name from vocabulary
    raw_rgb: Optional[tuple[int, int, int]] = None
    hex_code: Optional[str] = None
    confidence: float = 0.0
    is_ambiguous: bool = False
    notes: Optional[str] = None


class ImageColorAnalyzer:
    """
    Local, robust image color analyzer for product/saree images.
    
    Principles:
    - Pure local image analysis using PIL & NumPy (zero network calls, zero external APIs).
    - Samples central foreground crop to minimize background interference (e.g. mannequin/studio background).
    - Filters out extreme background pixels (near-white studio backdrop, near-black shadows).
    - Clusters prominent pixels and maps them into a standard canonical color vocabulary.
    - If color is ambiguous, multi-tonal, low-contrast, or unconfident, leaves canonical_color as None
      and flags is_ambiguous / requires_review.
    - NEVER claims deep segmentation; reports clear confidence and provenance.
    """

    # Canonical color categories defined in HSV space (Hue in degrees [0, 360], Saturation [0, 1], Value [0, 1])
    COLOR_MAP = [
        # Neutral shades
        ("White", None, 0.0, 0.15, 0.85, 1.0),
        ("Black", None, 0.0, 1.0, 0.0, 0.18),
        ("Grey", None, 0.0, 0.18, 0.18, 0.85),
        # Chromatic hues: (Name, (h_min, h_max), s_min, s_max, v_min, v_max)
        # Red spans 345-360 and 0-15
        ("Red", (345, 15), 0.35, 1.0, 0.25, 1.0),
        ("Maroon", (345, 15), 0.35, 1.0, 0.15, 0.55),
        ("Orange", (15, 42), 0.40, 1.0, 0.40, 1.0),
        ("Rust", (15, 38), 0.35, 1.0, 0.20, 0.60),
        ("Yellow", (42, 68), 0.35, 1.0, 0.45, 1.0),
        ("Mustard", (38, 55), 0.35, 1.0, 0.25, 0.70),
        ("Olive", (55, 80), 0.25, 1.0, 0.20, 0.65),
        ("Green", (80, 165), 0.25, 1.0, 0.20, 1.0),
        ("Teal", (165, 190), 0.25, 1.0, 0.20, 0.85),
        ("Cyan", (175, 200), 0.35, 1.0, 0.60, 1.0),
        ("Blue", (200, 255), 0.25, 1.0, 0.20, 1.0),
        ("Navy Blue", (210, 250), 0.30, 1.0, 0.15, 0.45),
        ("Purple", (255, 290), 0.25, 1.0, 0.20, 0.90),
        ("Magenta", (290, 325), 0.30, 1.0, 0.30, 1.0),
        ("Pink", (325, 345), 0.20, 1.0, 0.50, 1.0),
        ("Brown", (15, 40), 0.20, 0.75, 0.15, 0.50),
        ("Beige", (30, 50), 0.10, 0.35, 0.65, 0.95),
    ]

    @classmethod
    def analyze_image(
        cls,
        image_input: Union[str, Path, bytes, Image.Image],
        crop_center_ratio: float = 0.6,
        min_saturation: float = 0.12,
        min_value: float = 0.12,
        max_value: float = 0.95,
    ) -> ImageColorResult:
        """
        Analyze the prominent color of an image file or object.
        """
        try:
            if isinstance(image_input, Image.Image):
                img = image_input.convert("RGB")
            elif isinstance(image_input, bytes):
                import io
                img = Image.open(io.BytesIO(image_input)).convert("RGB")
            else:
                p = Path(image_input)
                if not p.exists():
                    return ImageColorResult(
                        status="FAILED",
                        notes=f"Image file not found: {p}",
                    )
                img = Image.open(p).convert("RGB")
        except Exception as e:
            return ImageColorResult(
                status="FAILED",
                notes=f"Failed to open image: {type(e).__name__}: {e}",
            )

        width, height = img.size
        if width < 10 or height < 10:
            return ImageColorResult(
                status="FAILED",
                notes="Image dimensions too small for reliable color analysis",
            )

        # 1. Take central crop to focus on the fabric / saree body and reduce studio borders
        cw = int(width * crop_center_ratio)
        ch = int(height * crop_center_ratio)
        left = (width - cw) // 2
        top = (height - ch) // 2
        cropped = img.crop((left, top, left + cw, top + ch))

        # Downsample for fast clustering and noise reduction
        cropped = cropped.resize((100, 100), Image.Resampling.LANCZOS)
        arr = np.array(cropped, dtype=np.float32) / 255.0  # Shape: (100, 100, 3)

        pixels = arr.reshape(-1, 3)  # Shape: (10000, 3)

        # 2. Convert to HSV
        # Vectorized RGB to HSV
        r = pixels[:, 0]
        g = pixels[:, 1]
        b = pixels[:, 2]

        maxc = np.maximum(np.maximum(r, g), b)
        minc = np.minimum(np.minimum(r, g), b)
        v = maxc
        delta = maxc - minc

        s = np.zeros_like(v)
        nonzero_v = v > 0
        s[nonzero_v] = delta[nonzero_v] / v[nonzero_v]

        h = np.zeros_like(v)
        nonzero_delta = delta > 0

        # Red is max
        idx_r = (maxc == r) & nonzero_delta
        h[idx_r] = ((g[idx_r] - b[idx_r]) / delta[idx_r]) % 6.0

        # Green is max
        idx_g = (maxc == g) & nonzero_delta
        h[idx_g] = ((b[idx_g] - r[idx_g]) / delta[idx_g]) + 2.0

        # Blue is max
        idx_b = (maxc == b) & nonzero_delta
        h[idx_b] = ((r[idx_b] - g[idx_b]) / delta[idx_b]) + 4.0

        h = h * 60.0  # degrees [0, 360]

        # 3. Filter out non-informative background (blown out white studio backgrounds or dark shadows)
        valid_mask = (v >= min_value) & (v <= max_value)
        # Saturated color pixels
        color_mask = valid_mask & (s >= min_saturation)

        if np.count_nonzero(color_mask) < (len(pixels) * 0.10):
            # Less than 10% chromatic pixels - check if primarily neutral (e.g. White, Black, Grey)
            if np.count_nonzero(valid_mask) < (len(pixels) * 0.15):
                return ImageColorResult(
                    status="BACKGROUND_ONLY",
                    is_ambiguous=True,
                    confidence=0.0,
                    notes="Image consists almost entirely of extreme highlights or shadows",
                )
            # Evaluate neutral dominant tone
            mean_v = float(np.mean(v[valid_mask]))
            if mean_v > 0.75:
                detected_color = "White"
            elif mean_v < 0.25:
                detected_color = "Black"
            else:
                detected_color = "Grey"

            return ImageColorResult(
                status="EXTRACTED",
                canonical_color=detected_color,
                raw_rgb=(int(mean_v * 255), int(mean_v * 255), int(mean_v * 255)),
                hex_code=f"#{int(mean_v*255):02x}{int(mean_v*255):02x}{int(mean_v*255):02x}",
                confidence=0.65,
                is_ambiguous=False,
                notes=f"Low-saturation neutral palette identified as {detected_color}",
            )

        # 4. We have sufficient chromatic pixels
        filtered_h = h[color_mask]
        filtered_s = s[color_mask]
        filtered_v = v[color_mask]
        filtered_rgb = pixels[color_mask]

        # Classify each pixel into standard color bucket
        votes: dict[str, list[int]] = {}
        for i in range(len(filtered_h)):
            px_h = filtered_h[i]
            px_s = filtered_s[i]
            px_v = filtered_v[i]
            c_name = cls._classify_hsv(px_h, px_s, px_v)
            if c_name:
                votes.setdefault(c_name, []).append(i)

        if not votes:
            return ImageColorResult(
                status="UNCERTAIN",
                is_ambiguous=True,
                confidence=0.2,
                notes="Chromatic pixels did not cluster into standard palette",
            )

        # Rank candidate colors by pixel percentage
        total_valid = len(filtered_h)
        sorted_candidates = sorted(
            [(c, len(indices) / total_valid, indices) for c, indices in votes.items()],
            key=lambda x: x[1],
            reverse=True,
        )

        top_color, top_ratio, top_indices = sorted_candidates[0]

        # Check for multi-colored / distracting multi-tonal image
        # If top color does not command at least 40% of foreground palette, or second color is very close
        if len(sorted_candidates) > 1:
            second_color, second_ratio, _ = sorted_candidates[1]
            if top_ratio < 0.40 or (top_ratio - second_ratio) < 0.15:
                return ImageColorResult(
                    status="UNCERTAIN",
                    is_ambiguous=True,
                    confidence=float(round(top_ratio, 2)),
                    notes=f"Multi-tonal image: '{top_color}' ({int(top_ratio*100)}%) vs '{second_color}' ({int(second_ratio*100)}%); requires manual review",
                )
        elif top_ratio < 0.35:
            return ImageColorResult(
                status="UNCERTAIN",
                is_ambiguous=True,
                confidence=float(round(top_ratio, 2)),
                notes=f"Low dominant color confidence ({int(top_ratio*100)}%); requires manual review",
            )

        # Compute representative RGB
        selected_rgb = filtered_rgb[top_indices]
        mean_rgb = np.mean(selected_rgb, axis=0) * 255.0
        rgb_tuple = (int(mean_rgb[0]), int(mean_rgb[1]), int(mean_rgb[2]))
        hex_str = f"#{rgb_tuple[0]:02x}{rgb_tuple[1]:02x}{rgb_tuple[2]:02x}"

        confidence = float(min(1.0, round(top_ratio * 1.2, 2)))

        return ImageColorResult(
            status="EXTRACTED",
            canonical_color=top_color,
            raw_rgb=rgb_tuple,
            hex_code=hex_str,
            confidence=confidence,
            is_ambiguous=False,
            notes=f"Dominant color '{top_color}' accounts for {int(top_ratio*100)}% of foreground palette",
        )

    @classmethod
    def _classify_hsv(cls, h: float, s: float, v: float) -> Optional[str]:
        """Classify HSV pixel into canonical color vocabulary."""
        # Specific hue ranges
        if (h >= 345 or h <= 15):
            if v < 0.40 and s > 0.40:
                return "Maroon"
            return "Red"
        if 15 < h <= 42:
            if v < 0.50 and s < 0.70:
                return "Brown" if s > 0.30 else "Rust"
            return "Orange"
        if 42 < h <= 68:
            if s > 0.30:
                if v < 0.65 and h < 55:
                    return "Mustard"
                return "Yellow"
            return "Beige"
        if 68 < h <= 85:
            return "Olive"
        if 85 < h <= 165:
            return "Green"
        if 165 < h <= 195:
            return "Teal"
        if 195 < h <= 255:
            if v < 0.45:
                return "Navy Blue"
            return "Blue"
        if 255 < h <= 295:
            return "Purple"
        if 295 < h <= 325:
            return "Magenta"
        if 325 < h < 345:
            return "Pink"

        return None
