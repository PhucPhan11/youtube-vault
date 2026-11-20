"""Tiện ích theo hệ điều hành: mở thư mục chứa file bằng trình quản lý file."""

from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path


def open_folder(path: Path | str, select: bool = True) -> None:
    """Mở thư mục chứa ``path``; nếu ``path`` là file và ``select`` thì chọn sẵn file đó.

    Ném ``FileNotFoundError`` nếu cả file lẫn thư mục cha đều không tồn tại.
    """
    target = Path(path).expanduser()
    is_file = target.is_file()
    folder = target.parent if (is_file or not target.exists()) else target
    if not folder.exists():
        raise FileNotFoundError(f"Không tìm thấy thư mục: {folder}")

    system = platform.system()
    if system == "Windows":
        if is_file and select:
            subprocess.Popen(["explorer", f"/select,{target}"])
        else:
            os.startfile(folder)  # type: ignore[attr-defined]
    elif system == "Darwin":
        args = ["open", "-R", str(target)] if is_file and select else ["open", str(folder)]
        subprocess.Popen(args)
    else:
        subprocess.Popen(["xdg-open", str(folder)])
