#!/usr/bin/env python3
"""Project CLI entry for the self-contained wiki-learning state tool."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "skills/wiki-learning/scripts/learning_state.py"
SPEC = importlib.util.spec_from_file_location("wiki_learning_state", SOURCE)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

if __name__ == "__main__":
    module.main(default_root=ROOT)
