# -*- coding: utf-8 -*-
"""兼容入口：实际逻辑在 workbench/entry/start_site.py。

保留根目录同名文件，使 启动.cmd、README 里的 `python start_site.py` 继续有效。
"""
from workbench.entry.start_site import main

if __name__ == "__main__":
    main()
