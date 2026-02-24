"""CoverGenerator — animated GIF cover images with futuristic neon aesthetic.

Generates eye-catching cover images for Hashnode articles.
Three styles: neon (default), matrix, gradient.

Tech stack: Pillow for rendering + frame assembly.
Optional: pycairo for advanced text glow, pygifsicle for optimization.

Size: 1600x840px (Hashnode recommended), optimized under 2MB.
Fallback: static PNG if GIF generation fails.
"""

import logging
import math
import random
import re
from pathlib import Path
from textwrap import wrap

from PIL import Image, ImageDraw, ImageFilter, ImageFont

logger = logging.getLogger(__name__)

# Canvas dimensions (Hashnode recommended)
WIDTH = 1600
HEIGHT = 840

# Color palettes
NEON_COLORS = {
    "bg": (10, 10, 15),
    "grid": (20, 40, 60),
    "text": (0, 255, 255),       # Cyan
    "glow": (0, 200, 255),       # Blue-cyan
    "accent": (255, 0, 200),     # Magenta
    "subtitle": (100, 200, 255),
}

MATRIX_COLORS = {
    "bg": (0, 10, 0),
    "rain": (0, 180, 0),
    "text": (200, 255, 200),
    "glow": (0, 255, 0),
}

GRADIENT_COLORS = {
    "start": (20, 0, 80),
    "end": (0, 60, 120),
    "text": (255, 255, 255),
    "accent": (100, 200, 255),
}


def _get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Get a font, falling back to default if custom fonts unavailable."""
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for path in font_paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


class CoverGenerator:
    """Generates animated GIF cover images for articles."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate(self, title: str, style: str = "neon", slug: str = "") -> Path:
        """Generate an animated GIF cover image.

        Args:
            title: Article title to display
            style: Visual style (neon, matrix, gradient)
            slug: Optional slug for filename

        Returns:
            Path to the generated GIF file
        """
        safe_slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", slug or "cover").strip("-") or "cover"
        filename = f"{safe_slug}.gif"
        output_path = self.output_dir / filename
        # Prevent path traversal
        if self.output_dir.resolve() not in output_path.resolve().parents and output_path.resolve().parent != self.output_dir.resolve():
            raise ValueError(f"Path traversal detected: {filename}")

        try:
            if style == "matrix":
                frames = self._render_matrix_frames(title)
            elif style == "gradient":
                frames = self._render_gradient_frames(title)
            else:
                frames = self._render_neon_frames(title)

            if not frames:
                logger.warning("No frames generated. Falling back to static PNG.")
                return self._render_static_fallback(title, output_path)

            # Save as animated GIF
            frames[0].save(
                output_path,
                save_all=True,
                append_images=frames[1:],
                duration=100,  # 100ms per frame = 10fps
                loop=0,
                optimize=True,
            )

            # Try to optimize with gifsicle if available
            output_path = self._optimize_gif(output_path)

            file_size = output_path.stat().st_size
            logger.info(
                "Cover generated: %s (%.1f KB, %d frames, style=%s)",
                output_path.name, file_size / 1024, len(frames), style,
            )
            return output_path

        except Exception as e:
            logger.exception("GIF generation failed: %s. Using static fallback.", e)
            return self._render_static_fallback(title, output_path.with_suffix(".png"))

    def _render_neon_frames(self, title: str, num_frames: int = 20) -> list[Image.Image]:
        """Render neon glow animation frames.

        Animated pulsing glow effect on title text over dark grid background.
        """
        frames: list[Image.Image] = []
        font_title = _get_font(72, bold=True)
        font_subtitle = _get_font(28)

        # Wrap title to fit, limit to 3 lines
        wrapped_title = wrap(title, width=28)
        display_lines = wrapped_title[:3]

        for i in range(num_frames):
            img = Image.new("RGB", (WIDTH, HEIGHT), NEON_COLORS["bg"])
            draw = ImageDraw.Draw(img)

            # Draw grid lines
            phase = i * 4
            for x in range(0, WIDTH, 60):
                opacity = int(30 + 10 * math.sin((x + phase) * 0.05))
                draw.line([(x, 0), (x, HEIGHT)], fill=(*NEON_COLORS["grid"][:2], opacity), width=1)
            for y in range(0, HEIGHT, 60):
                opacity = int(30 + 10 * math.sin((y + phase) * 0.05))
                draw.line([(0, y), (WIDTH, y)], fill=(*NEON_COLORS["grid"][:2], opacity), width=1)

            # Glow intensity oscillates
            glow_intensity = 0.6 + 0.4 * math.sin(2 * math.pi * i / num_frames)

            # Draw title text with glow
            y_start = HEIGHT // 2 - len(display_lines) * 45
            for line_idx, line in enumerate(display_lines):
                bbox = draw.textbbox((0, 0), line, font=font_title)
                text_width = bbox[2] - bbox[0]
                x = (WIDTH - text_width) // 2
                y = y_start + line_idx * 90

                # Glow layer (draw text larger, blur, overlay)
                glow_img = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
                glow_draw = ImageDraw.Draw(glow_img)
                glow_color = tuple(int(c * glow_intensity) for c in NEON_COLORS["glow"])
                glow_draw.text((x, y), line, font=font_title, fill=glow_color)
                glow_img = glow_img.filter(ImageFilter.GaussianBlur(radius=8))

                # Blend glow onto main image
                img = self._blend_images(img, glow_img, alpha=0.7)
                draw = ImageDraw.Draw(img)

                # Draw crisp text on top
                draw.text((x, y), line, font=font_title, fill=NEON_COLORS["text"])

            # Subtitle: "NETANEL" at bottom
            subtitle = "NETANEL"
            bbox = draw.textbbox((0, 0), subtitle, font=font_subtitle)
            sw = bbox[2] - bbox[0]
            draw.text(
                ((WIDTH - sw) // 2, HEIGHT - 100),
                subtitle,
                font=font_subtitle,
                fill=NEON_COLORS["subtitle"],
            )

            # Accent line
            line_y = HEIGHT - 60
            accent_alpha = int(200 * glow_intensity)
            draw.line(
                [(WIDTH // 4, line_y), (3 * WIDTH // 4, line_y)],
                fill=(*NEON_COLORS["accent"][:2], accent_alpha),
                width=2,
            )

            frames.append(img)

        return frames

    def _render_matrix_frames(self, title: str, num_frames: int = 20) -> list[Image.Image]:
        """Render matrix rain animation with title overlay."""
        frames: list[Image.Image] = []
        font_title = _get_font(72, bold=True)
        font_rain = _get_font(16)

        wrapped_title = wrap(title, width=28)

        # Initialize rain columns
        columns = WIDTH // 20
        drops = [random.randint(-HEIGHT, 0) for _ in range(columns)]

        for i in range(num_frames):
            img = Image.new("RGB", (WIDTH, HEIGHT), MATRIX_COLORS["bg"])
            draw = ImageDraw.Draw(img)

            # Draw rain
            for col in range(columns):
                x = col * 20
                y = drops[col]
                for row in range(0, HEIGHT, 20):
                    if y + row > 0 and y + row < HEIGHT:
                        char = chr(random.randint(0x30, 0x39))  # Digits
                        brightness = max(0, 255 - abs(row) * 3)
                        color = (0, brightness, 0)
                        draw.text((x, y + row), char, font=font_rain, fill=color)
                drops[col] = (drops[col] + 15) % (HEIGHT + 200) - 200

            # Semi-transparent overlay for readability
            overlay = Image.new("RGB", (WIDTH, HEIGHT), MATRIX_COLORS["bg"])
            img = self._blend_images(img, overlay, alpha=0.4)
            draw = ImageDraw.Draw(img)

            # Draw title
            y_start = HEIGHT // 2 - len(wrapped_title) * 45
            for line_idx, line in enumerate(wrapped_title[:3]):
                bbox = draw.textbbox((0, 0), line, font=font_title)
                text_width = bbox[2] - bbox[0]
                x = (WIDTH - text_width) // 2
                y = y_start + line_idx * 90
                draw.text((x, y), line, font=font_title, fill=MATRIX_COLORS["text"])

            frames.append(img)

        return frames

    def _render_gradient_frames(self, title: str, num_frames: int = 20) -> list[Image.Image]:
        """Render smooth gradient animation with clean typography."""
        frames: list[Image.Image] = []
        font_title = _get_font(72, bold=True)
        font_subtitle = _get_font(28)

        wrapped_title = wrap(title, width=28)

        for i in range(num_frames):
            img = Image.new("RGB", (WIDTH, HEIGHT))
            draw = ImageDraw.Draw(img)

            # Animated gradient
            phase = 2 * math.pi * i / num_frames
            for y in range(HEIGHT):
                ratio = y / HEIGHT
                # Shift gradient phase for animation
                shifted_ratio = (ratio + 0.1 * math.sin(phase)) % 1.0
                r = int(GRADIENT_COLORS["start"][0] * (1 - shifted_ratio) +
                        GRADIENT_COLORS["end"][0] * shifted_ratio)
                g = int(GRADIENT_COLORS["start"][1] * (1 - shifted_ratio) +
                        GRADIENT_COLORS["end"][1] * shifted_ratio)
                b = int(GRADIENT_COLORS["start"][2] * (1 - shifted_ratio) +
                        GRADIENT_COLORS["end"][2] * shifted_ratio)
                draw.line([(0, y), (WIDTH, y)], fill=(r, g, b))

            # Draw title
            y_start = HEIGHT // 2 - len(wrapped_title) * 45
            for line_idx, line in enumerate(wrapped_title[:3]):
                bbox = draw.textbbox((0, 0), line, font=font_title)
                text_width = bbox[2] - bbox[0]
                x = (WIDTH - text_width) // 2
                y = y_start + line_idx * 90
                # Shadow
                draw.text((x + 3, y + 3), line, font=font_title, fill=(0, 0, 0))
                draw.text((x, y), line, font=font_title, fill=GRADIENT_COLORS["text"])

            # Subtitle
            subtitle = "NETANEL"
            bbox = draw.textbbox((0, 0), subtitle, font=font_subtitle)
            sw = bbox[2] - bbox[0]
            draw.text(
                ((WIDTH - sw) // 2, HEIGHT - 100),
                subtitle,
                font=font_subtitle,
                fill=GRADIENT_COLORS["accent"],
            )

            frames.append(img)

        return frames

    def _render_static_fallback(self, title: str, output_path: Path) -> Path:
        """Render a static PNG cover as fallback."""
        output_path = output_path.with_suffix(".png")
        img = Image.new("RGB", (WIDTH, HEIGHT), NEON_COLORS["bg"])
        draw = ImageDraw.Draw(img)
        font_title = _get_font(72, bold=True)
        font_subtitle = _get_font(28)

        wrapped_title = wrap(title, width=28)
        y_start = HEIGHT // 2 - len(wrapped_title) * 45
        for line_idx, line in enumerate(wrapped_title[:3]):
            bbox = draw.textbbox((0, 0), line, font=font_title)
            text_width = bbox[2] - bbox[0]
            x = (WIDTH - text_width) // 2
            y = y_start + line_idx * 90
            draw.text((x, y), line, font=font_title, fill=NEON_COLORS["text"])

        subtitle = "NETANEL"
        bbox = draw.textbbox((0, 0), subtitle, font=font_subtitle)
        sw = bbox[2] - bbox[0]
        draw.text(
            ((WIDTH - sw) // 2, HEIGHT - 100),
            subtitle,
            font=font_subtitle,
            fill=NEON_COLORS["subtitle"],
        )

        img.save(output_path, "PNG", optimize=True)
        logger.info("Static fallback cover generated: %s", output_path.name)
        return output_path

    @staticmethod
    def _blend_images(base: Image.Image, overlay: Image.Image, alpha: float) -> Image.Image:
        """Blend two images with given alpha."""
        return Image.blend(base, overlay, alpha)

    @staticmethod
    def _optimize_gif(path: Path) -> Path:
        """Optimize GIF with gifsicle if available."""
        try:
            import subprocess
            result = subprocess.run(
                ["gifsicle", "--optimize=3", "--colors", "128",
                 str(path), "-o", str(path)],
                capture_output=True, timeout=30,
            )
            if result.returncode == 0:
                logger.info("GIF optimized with gifsicle.")
            else:
                logger.warning("gifsicle failed: %s", result.stderr.decode()[:200])
        except FileNotFoundError:
            logger.info("gifsicle not found. Skipping GIF optimization.")
        except Exception as e:
            logger.warning("GIF optimization error: %s", e)
        return path
