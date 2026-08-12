#!/usr/bin/env python3
"""
Optik Analiz arayüzünü başlatır.

Kullanım:
    python3 run_gui.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gui.main_window import main

if __name__ == "__main__":
    main()
