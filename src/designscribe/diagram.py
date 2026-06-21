"""Diagram renderer — Mermaid to image via mmdc CLI.

If mmdc is not available, saves the .mmd file and reports the path.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


def render_mermaid(
    mermaid_text: str,
    output_path: str,
    fmt: str = "png",
) -> str:
    """Render Mermaid syntax to an image file.

    Args:
        mermaid_text: Mermaid diagram syntax
        output_path: Where to save the output (directory or full path)
        fmt: Output format (png, svg, pdf)

    Returns:
        Path to the rendered file (.mmd if mmdc unavailable)
    """
    if not mermaid_text or not mermaid_text.strip():
        return ""

    # Ensure output directory exists
    out = Path(output_path)
    if out.suffix:
        # output_path is a full file path
        out.parent.mkdir(parents=True, exist_ok=True)
    else:
        # output_path is a directory
        out.mkdir(parents=True, exist_ok=True)
        out = out / f"diagram.{fmt}"

    # Check for mmdc
    mmdc = shutil.which("mmdc")
    if not mmdc:
        # Save .mmd file as fallback
        mmd_path = out.with_suffix(".mmd")
        mmd_path.write_text(mermaid_text, encoding="utf-8")
        return str(mmd_path)

    # Write mermaid to temp file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".mmd", delete=False) as tmp:
        tmp.write(mermaid_text)
        tmp_path = tmp.name

    try:
        result = subprocess.run(
            [mmdc, "-i", tmp_path, "-o", str(out), "-b", "transparent"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            # mmdc failed — save .mmd as fallback
            mmd_path = out.with_suffix(".mmd")
            mmd_path.write_text(mermaid_text, encoding="utf-8")
            return str(mmd_path)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        mmd_path = out.with_suffix(".mmd")
        mmd_path.write_text(mermaid_text, encoding="utf-8")
        return str(mmd_path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    return str(out)
