import base64
import os
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]


def create_icon():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
        size = 256
        pix = QPixmap(size, size)
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#1e1e2e"))
        painter.drawRoundedRect(0, 0, size, size, 48, 48)
        painter.setBrush(QColor("#4caf50"))
        painter.drawEllipse(32, 32, 192, 192)
        painter.setPen(QColor("#ffffff"))
        font = QFont("Arial")
        font.setBold(True)
        font.setPixelSize(96)
        painter.setFont(font)
        painter.drawText(pix.rect(), Qt.AlignCenter, "AFK")
        painter.end()
        target = ROOT / "gui" / "afk.ico"
        if pix.save(str(target), "ICO"):
            print("icon created:", target)
            return str(target)
        print("icon save failed (ICO writer unavailable)")
    except Exception as exc:
        print("icon creation skipped:", exc)
    return ""


def run_ps(command):
    encoded = base64.b64encode(command.encode("utf-16-le")).decode()
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        if proc.stdout.strip():
            print(proc.stdout.strip()[:4000])
    except Exception:
        pass
    if proc.returncode != 0:
        try:
            print(proc.stderr.strip()[:2000])
        except Exception:
            pass


def create_shortcuts(icon_path):
    desktop = os.path.join(os.environ.get("USERPROFILE", ""), "Desktop")
    lines = [
        "$ws = New-Object -ComObject WScript.Shell",
        "function New-AfkShortcut([string]$path, [string]$arguments, [string]$desc, [string]$icon) {",
        "  $lnk = $ws.CreateShortcut($path)",
        "  $lnk.TargetPath = \"$env:SystemRoot\\System32\\wscript.exe\"",
        "  $lnk.Arguments = $arguments",
        "  $lnk.WorkingDirectory = '{root}'".format(root=ROOT),
        "  $lnk.Description = $desc",
        "  if ($icon -and (Test-Path $icon)) { $lnk.IconLocation = $icon }",
        "  $lnk.Save()",
        "  Write-Host ('created: ' + $path)",
        "}",
    ]
    gui_vbs = ROOT / "tools" / "launch_gui.vbs"
    admin_vbs = ROOT / "tools" / "launch_admin.vbs"
    icon = icon_path or ""
    targets = [
        ("{0}\\Auto-AFK 自动挂机.lnk".format(ROOT), gui_vbs, "Auto-AFK 自动挂机", True),
        ("{0}\\Auto-AFK 自动挂机(管理员).lnk".format(ROOT), admin_vbs, "Auto-AFK 自动挂机(管理员)", True),
    ]
    if desktop and os.path.isdir(desktop):
        targets += [
            ("{0}\\Auto-AFK 自动挂机.lnk".format(desktop), gui_vbs, "Auto-AFK 自动挂机", True),
            ("{0}\\Auto-AFK 自动挂机(管理员).lnk".format(desktop), admin_vbs, "Auto-AFK 自动挂机(管理员)", True),
        ]
    for path, vbs, desc, autorun in targets:
        args = '"{0}"'.format(vbs)
        if autorun and vbs == gui_vbs:
            args += " --autorun"
        args = args.replace("'", "''")
        lines.append("New-AfkShortcut '{}' '{}' '{}' '{}'".format(path.replace("'", "''"), args, desc, icon))
    run_ps("\r\n".join(lines))


def main():
    icon = create_icon()
    create_shortcuts(icon)
    return 0


if __name__ == "__main__":
    sys.exit(main())
