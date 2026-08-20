#!/usr/bin/python3
"""Stable entry point for the Stream 100 PipeWire mixer."""

from pathlib import Path
import runpy


implementation = Path(__file__).resolve().with_name("stream100-mixer-alpha.py")
runpy.run_path(str(implementation), run_name="__main__")
