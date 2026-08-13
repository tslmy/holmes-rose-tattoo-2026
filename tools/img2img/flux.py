#!/usr/bin/env python3
"""Compatibility entry point for the native FLUX backend."""
from pathlib import Path
import runpy
runpy.run_path(str(Path(__file__).resolve().parents[1] / "flux_redraw_rosetattoo_backgrounds.py"), run_name="__main__")
