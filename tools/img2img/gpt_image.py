#!/usr/bin/env python3
"""Compatibility entry point for the optional GPT Image API backend."""
from pathlib import Path
import runpy
runpy.run_path(str(Path(__file__).resolve().parents[1] / "openai_image_rosetattoo_pass.py"), run_name="__main__")
