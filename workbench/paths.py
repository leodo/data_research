# -*- coding: utf-8 -*-
"""项目根与常用目录的唯一来源。

所有模块通过本文件取路径，禁止再用 Path(__file__).parent 推算 data/ 等目录，
否则文件一移动就会读写到错误位置。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
WEB = ROOT / "web"
VENDOR = ROOT / "vendor"
REPORTS = ROOT / "reports"
DOCS = ROOT / "docs"
