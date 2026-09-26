import os
import subprocess


def hidden_kwargs():
    if os.name != "nt":
        return {}
    kwargs = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        kwargs["startupinfo"] = startupinfo
    except Exception:
        pass
    return kwargs


def run(*args, **kwargs):
    merged = hidden_kwargs()
    merged.update(kwargs)
    return subprocess.run(*args, **merged)


def popen(*args, **kwargs):
    merged = hidden_kwargs()
    merged.update(kwargs)
    return subprocess.Popen(*args, **merged)
