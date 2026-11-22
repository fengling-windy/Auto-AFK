"""主题令牌与 QSS 生成。

历史：整个界面只有一套深色 QSS（`gui/modern.py` 里 400 多行硬编码十六进制
颜色），想加浅色主题只能复制整段样式再逐行改色。这里把颜色抽成「设计令牌」
（`$window` / `$card` / `$accent` …），`modern.APP_QSS_TEMPLATE` 只写令牌，
由 `build_qss()` 按主题替换成真正的颜色。

新增主题只需要在 `THEMES` 里补一份令牌表；令牌缺项会回退到深色值并在
`check_theme` 里报出来（冒烟测试用它守完整性）。
"""

# 深色（原有配色，改动即等于改视觉，非必要不要动）
DARK = {
    "window": "#0b0d12",
    "panel": "#10141d",
    "card": "#141926",
    "card_border": "#212b3c",
    "border": "#1d2432",
    "sidebar_bg": "#0d1017",
    "control_bg": "#1a2130",
    "control_border": "#2a3547",
    "control_bg_hover": "#243048",
    "control_bg_press": "#151c2a",
    "control_border_hover": "#3f5173",
    "accent": "#3d7dfa",
    "accent_hover": "#5594ff",
    "accent_soft": "#4c8dff",
    "danger_text": "#ff92a2",
    "danger_bg": "#251821",
    "danger_bg_hover": "#33212c",
    "danger_bg_press": "#1e1319",
    "danger_border": "#48242f",
    "input_bg_deep": "#0a0c11",
    "input_bg": "#151b28",
    "input_border": "#242f42",
    "input_border_hover": "#33415c",
    "selection": "#2b4a7a",
    "text_strong": "#ecf1fa",
    "text": "#d7deec",
    "text_control": "#ccd6ea",
    "section_title": "#b9c4da",
    "nav_text": "#8b97ad",
    "nav_text_hover": "#cdd7ea",
    "nav_text_checked": "#eef3fc",
    "nav_checked_bg": "#1a2334",
    "text_muted": "#7f8aa3",
    "text_dim": "#5c667d",
    "text_placeholder": "#5f6a82",
    "text_disabled": "#59627a",
    "disabled_bg": "#161c28",
    "disabled_border": "#232c3d",
    "scroll_handle": "#2b3547",
    "scroll_handle_hover": "#3d4d6b",
    "tooltip_bg": "#1a2130",
    "tooltip_text": "#dfe6f5",
    "on_accent": "#ffffff",
    "on_selection": "#ffffff",
    "overlay_subtle": "rgba(255, 255, 255, 0.04)",
    "overlay_nav": "rgba(255, 255, 255, 0.045)",
    "overlay_hover": "rgba(255, 255, 255, 0.06)",
}

# 浅色：同一套令牌换色，行高/圆角/字号完全共享，切换主题不做布局改动
LIGHT = {
    "window": "#f3f6fb",
    "panel": "#ffffff",
    "card": "#ffffff",
    "card_border": "#e2e8f2",
    "border": "#e3e8f1",
    "sidebar_bg": "#eef2f9",
    "control_bg": "#ffffff",
    "control_border": "#d7dee9",
    "control_bg_hover": "#eef3fb",
    "control_bg_press": "#e3eaf6",
    "control_border_hover": "#b9c4d6",
    "accent": "#2563eb",
    "accent_hover": "#3b82f6",
    "accent_soft": "#2563eb",
    "danger_text": "#d92d20",
    "danger_bg": "#fef3f2",
    "danger_bg_hover": "#fee4e2",
    "danger_bg_press": "#fecdca",
    "danger_border": "#fecdca",
    "input_bg_deep": "#ffffff",
    "input_bg": "#ffffff",
    "input_border": "#d7dee9",
    "input_border_hover": "#b9c4d6",
    "selection": "#dbeafe",
    "text_strong": "#0f172a",
    "text": "#1f2937",
    "text_control": "#334155",
    "section_title": "#475569",
    "nav_text": "#5b6779",
    "nav_text_hover": "#0f172a",
    "nav_text_checked": "#0b1220",
    "nav_checked_bg": "#dbe7fb",
    "text_muted": "#64748b",
    "text_dim": "#7d879c",
    "text_placeholder": "#94a3b8",
    "text_disabled": "#a3adbe",
    "disabled_bg": "#eef1f6",
    "disabled_border": "#dde3ec",
    "scroll_handle": "#c8d1e0",
    "scroll_handle_hover": "#aab6c9",
    "tooltip_bg": "#0f172a",
    "tooltip_text": "#f8fafc",
    "on_accent": "#ffffff",
    "on_selection": "#0f172a",
    "overlay_subtle": "rgba(15, 23, 42, 0.04)",
    "overlay_nav": "rgba(15, 23, 42, 0.05)",
    "overlay_hover": "rgba(15, 23, 42, 0.06)",
}

THEMES = {"dark": DARK, "light": LIGHT}

# 主题选择器展示用（顺序即下拉框顺序）
THEME_LABELS = {"dark": "深色（默认）", "light": "浅色"}

DEFAULT_THEME = "dark"

# 按钮配色。以前是 gui/modern.py 里的模块常量 _BUTTON_VARIANTS，只够深色用；
# 现在按主题给两套。
BUTTONS = {
    "dark": {
        "default": {
            "bg": "#1a2130", "bg_hover": "#243048", "bg_press": "#151c2a",
            "border": "#2a3547", "border_hover": "#3f5173", "text": "#ccd6ea",
            "ripple": "#7fb0ff", "radius": 9.0, "gradient": 0.0,
        },
        "primary": {
            "bg": "#3d7dfa", "bg_hover": "#5594ff", "bg_press": "#2e6ae6",
            "border": "#3d7dfa", "border_hover": "#5594ff", "text": "#ffffff",
            "ripple": "#ffffff", "radius": 9.0, "gradient": 0.07,
        },
        "danger": {
            "bg": "#251821", "bg_hover": "#33212c", "bg_press": "#1e1319",
            "border": "#48242f", "border_hover": "#5e2f3d", "text": "#ff92a2",
            "ripple": "#ff92a2", "radius": 9.0, "gradient": 0.0,
        },
        "mini": {
            "bg": "#1a2130", "bg_hover": "#243048", "bg_press": "#151c2a",
            "border": "#2a3547", "border_hover": "#3f5173", "text": "#ccd6ea",
            "ripple": "#7fb0ff", "radius": 7.0, "gradient": 0.0,
        },
        "parallel": {
            "bg": "#1b2740", "bg_hover": "#25355a", "bg_press": "#16203a",
            "border": "#2f4a7a", "border_hover": "#436699", "text": "#8fc0ff",
            "ripple": "#8fc0ff", "radius": 7.0, "gradient": 0.0,
        },
    },
    "light": {
        "default": {
            "bg": "#ffffff", "bg_hover": "#f1f6fd", "bg_press": "#e6edf9",
            "border": "#d7dee9", "border_hover": "#b9c4d6", "text": "#334155",
            "ripple": "#93c5fd", "radius": 9.0, "gradient": 0.0,
        },
        "primary": {
            "bg": "#2563eb", "bg_hover": "#3b82f6", "bg_press": "#1d4ed8",
            "border": "#2563eb", "border_hover": "#3b82f6", "text": "#ffffff",
            "ripple": "#ffffff", "radius": 9.0, "gradient": 0.05,
        },
        "danger": {
            "bg": "#fef3f2", "bg_hover": "#fee4e2", "bg_press": "#fecdca",
            "border": "#fecdca", "border_hover": "#fda29b", "text": "#d92d20",
            "ripple": "#f97066", "radius": 9.0, "gradient": 0.0,
        },
        "mini": {
            "bg": "#ffffff", "bg_hover": "#f1f6fd", "bg_press": "#e6edf9",
            "border": "#d7dee9", "border_hover": "#b9c4d6", "text": "#334155",
            "ripple": "#93c5fd", "radius": 7.0, "gradient": 0.0,
        },
        "parallel": {
            "bg": "#eaf2ff", "bg_hover": "#dbe9ff", "bg_press": "#cadcfc",
            "border": "#b6d0f7", "border_hover": "#8db6ef", "text": "#1d4ed8",
            "ripple": "#93c5fd", "radius": 7.0, "gradient": 0.0,
        },
    },
}


def resolve(theme):
    """取主题令牌表；未知主题回退深色（不要让界面因为写错主题名起不来）。"""
    return THEMES.get(str(theme or "").strip().lower(), DARK)


def normalize_theme(theme):
    """把任意输入归一成合法主题名。"""
    name = str(theme or "").strip().lower()
    return name if name in THEMES else DEFAULT_THEME


def button_variants(theme):
    return BUTTONS.get(normalize_theme(theme), BUTTONS[DEFAULT_THEME])


def check_theme(theme):
    """返回该主题相对深色缺失/多余的令牌名（冒烟测试用来守完整性）。"""
    tokens = resolve(theme)
    missing = sorted(set(DARK) - set(tokens))
    extra = sorted(set(tokens) - set(DARK))
    return missing, extra


def with_alpha(color, alpha):
    """把 #rrggbb 或 rgba(...) 转成指定不透明度的 rgba()。"""
    percent = max(0, min(100, int(round(alpha))))
    if color.startswith("rgba(") or color.startswith("rgb("):
        parts = color[color.index("(") + 1 : color.rindex(")")].split(",")
        r, g, b = (int(float(p)) for p in parts[:3])
        return "rgba({0}, {1}, {2}, {3:.3f})".format(r, g, b, percent / 100.0)
    text = color.lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    try:
        r = int(text[0:2], 16)
        g = int(text[2:4], 16)
        b = int(text[4:6], 16)
    except (ValueError, IndexError):
        return color
    return "rgba({0}, {1}, {2}, {3:.3f})".format(r, g, b, percent / 100.0)
