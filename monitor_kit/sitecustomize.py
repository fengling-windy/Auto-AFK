import os

if os.environ.get("AFK_MONITOR") == "1":
    try:
        import afk_hook

        afk_hook.install()
    except Exception:
        pass
