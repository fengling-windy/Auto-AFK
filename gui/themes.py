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
    "window": "#090b10",
    "panel": "#0f1219",
    "card": "#131722",
    "card_border": "#1f2636",
    "border": "#1a202e",
    "sidebar_bg": "#0b0e14",
    "control_bg": "#171d2b",
    "control_border": "#263044",
    "control_bg_hover": "#20293d",
    "control_bg_press": "#131824",
    "control_border_hover": "#384866",
    "accent": "#4f86f7",
    "accent_hover": "#6698ff",
    "accent_soft": "#4f86f7",
    "danger_text": "#ff92a2",
    "danger_bg": "#24161f",
    "danger_bg_hover": "#311e2a",
    "danger_bg_press": "#1c1118",
    "danger_border": "#46222d",
    "input_bg_deep": "#090b10",
    "input_bg": "#131824",
    "input_border": "#222b3d",
    "input_border_hover": "#33415c",
    "selection": "#284675",
    "text_strong": "#f1f5fc",
    "text": "#dce3f0",
    "text_control": "#d0daec",
    "section_title": "#c0cbe0",
    "nav_text": "#8b97ad",
    "nav_text_hover": "#dce4f2",
    "nav_text_checked": "#f4f7fd",
    "nav_checked_bg": "#182132",
    "text_muted": "#8490a8",
    "text_dim": "#5e6880",
    "text_placeholder": "#5d6880",
    "text_disabled": "#555f75",
    "disabled_bg": "#141924",
    "disabled_border": "#202838",
    "scroll_handle": "#283244",
    "scroll_handle_hover": "#3a4966",
    "tooltip_bg": "#171d2b",
    "tooltip_text": "#e4eaf7",
    "on_accent": "#ffffff",
    "on_selection": "#ffffff",
    "overlay_subtle": "rgba(255, 255, 255, 0.038)",
    "overlay_nav": "rgba(255, 255, 255, 0.048)",
    "overlay_hover": "rgba(255, 255, 255, 0.065)",
    # 单色小图标（拖动把手、关闭、打开仓库…）。这些是自绘位图，QSS 的 $nav_text
    # 换不到它们身上，必须按主题给：写死 #8b97ad / #7f8aa3 时，浅色白卡上的
    # 对比度只有 2.95 / 3.46，图标淡到几乎看不见。
    "icon": "#8b97ad",
    "icon_subtle": "#7f8aa3",
}

# 浅色：参考 Apple / Linear 极简中性灰白层级，行高/圆角/字号完全共享
LIGHT = {
    "window": "#f5f7fb",
    "panel": "#ffffff",
    "card": "#ffffff",
    "card_border": "#e4e9f2",
    "border": "#e6ebf3",
    "sidebar_bg": "#eff3f9",
    "control_bg": "#ffffff",
    "control_border": "#d8e0ec",
    "control_bg_hover": "#f2f6fc",
    "control_bg_press": "#e6eef9",
    "control_border_hover": "#bcc7da",
    "accent": "#2563eb",
    "accent_hover": "#3b82f6",
    "accent_soft": "#2563eb",
    "danger_text": "#d92d20",
    "danger_bg": "#fef3f2",
    "danger_bg_hover": "#fee4e2",
    "danger_bg_press": "#fecdca",
    "danger_border": "#fecdca",
    "input_bg_deep": "#f9fafc",
    "input_bg": "#ffffff",
    "input_border": "#d8e0ec",
    "input_border_hover": "#bcc7da",
    "selection": "#dbeafe",
    "text_strong": "#0f172a",
    "text": "#1e293b",
    "text_control": "#334155",
    "section_title": "#334155",
    "nav_text": "#586578",
    "nav_text_hover": "#0f172a",
    "nav_text_checked": "#0b1220",
    "nav_checked_bg": "#dfe9fb",
    "text_muted": "#64748b",
    "text_dim": "#78849b",
    "text_placeholder": "#94a3b8",
    "text_disabled": "#a3adbe",
    "disabled_bg": "#f1f4f9",
    "disabled_border": "#e0e6ef",
    "scroll_handle": "#cbd4e3",
    "scroll_handle_hover": "#aebacf",
    "tooltip_bg": "#0f172a",
    "tooltip_text": "#f8fafc",
    "on_accent": "#ffffff",
    "on_selection": "#0f172a",
    "overlay_subtle": "rgba(15, 23, 42, 0.036)",
    "overlay_nav": "rgba(15, 23, 42, 0.048)",
    "overlay_hover": "rgba(15, 23, 42, 0.065)",
    # 浅色主题的白卡上要更深的灰才看得见（#8b97ad / #7f8aa3 只有 2.95 / 3.46）
    "icon": "#586578",
    "icon_subtle": "#64748b",
}

THEMES = {"dark": DARK, "light": LIGHT}

# 主题选择器展示用（顺序即下拉框顺序）
THEME_LABELS = {"dark": "深色（默认）", "light": "浅色"}

DEFAULT_THEME = "dark"

# 状态语义色（运行结果、链状态）。这一组必须是「按主题给」，不能只用一套：
# 深色底上的 #34d399 / #60a5fa 在白底上对比度只有 1.9 / 2.5，浅色主题下状态
# 文字几乎看不见（实测卡片底色 #ffffff 时 7 个状态全低于 4.5:1）。
# 下面浅色那一列是按 WCAG AA（≥4.5:1）挑过的深饱和同类色。
STATUS_PALETTE = {
    "dark": {
        "success": "#34d399",
        "success_unverified": "#a3e635",
        "running": "#60a5fa",
        "pending": "#7d879c",
        "failed": "#f87171",
        "hang": "#fb923c",
        "stopped": "#c084fc",
        "log_error": "#f87171",
        "log_warn": "#fbbf24",
    },
    "light": {
        "success": "#047857",
        "success_unverified": "#4d7c0f",
        "running": "#1d4ed8",
        "pending": "#475569",
        "failed": "#b91c1c",
        "hang": "#b45309",
        "stopped": "#6d28d9",
        "log_error": "#b91c1c",
        "log_warn": "#a16207",
    },
}

# 链状态只有五种，取上面同含义的那几个，保证「卡片运行中」与「链运行中」
# 是同一个颜色。
CHAIN_STATES = {
    "idle": "pending",
    "running": "running",
    "success": "success",
    "failed": "failed",
    "stopped": "stopped",
}

# 项目身份色（卡片顶部色条 + 实时日志里该项目的前缀）。同样必须按主题给：
# 深色那列是为 #0a0c11 的黑底挑的亮色，搬到浅色主题的白底日志区上对比度只剩
# 1.4–3.5（BetterGI #ffd54f 1.41、MaaEnd #ffb74d 1.73），前缀基本糊成一片。
# 浅色这一列是同色系里更深的一档，白底上全部 ≥4.5:1。
PROJECT_PALETTE = {
    "dark": {
        "March7thAssistant": "#4fc3f7",
        "BAAH": "#81c784",
        "MaaEnd": "#ffb74d",
        "ZenlessZoneZero-OneDragon": "#b388ff",
        "BetterGI": "#ffd54f",
        "core": "#ce93d8",
        "emulator": "#4db6ac",
        "update": "#90a4ae",
    },
    "light": {
        "March7thAssistant": "#0369a1",
        "BAAH": "#15803d",
        "MaaEnd": "#b45309",
        "ZenlessZoneZero-OneDragon": "#6d28d9",
        "BetterGI": "#854d0e",
        "core": "#86198f",
        "emulator": "#0f766e",
        "update": "#475569",
    },
}

# 注册表里没登记过、也没在 gui.project_colors 里配色的项目，按名字哈希落到
# 下面这一串里。同样两套：深色是亮色板，浅色是白底上看得见的深色板。
PROJECT_FALLBACK_PALETTE = {
    "dark": [
        "#4fc3f7",
        "#81c784",
        "#ffb74d",
        "#ce93d8",
        "#4db6ac",
        "#f06292",
        "#aed581",
        "#7986cb",
    ],
    "light": [
        "#0369a1",
        "#15803d",
        "#b45309",
        "#86198f",
        "#0f766e",
        "#be123c",
        "#3f6212",
        "#4338ca",
    ],
}


def status_color(theme, state):
    """取某个主题下某状态的颜色；未知状态回退该主题的 pending。"""
    table = STATUS_PALETTE.get(normalize_theme(theme), STATUS_PALETTE[DEFAULT_THEME])
    key = str(state)
    if key in table:
        return table[key]
    # 主题表缺这一项时回退到深色同名项，再不行就用本主题的 pending——
    # 不能直接回深色的 pending，否则浅色主题下会混进一个深色值。
    return STATUS_PALETTE[DEFAULT_THEME].get(key, table["pending"])


def chain_color(theme, state):
    """取某个主题下某「链状态」的颜色。"""
    return status_color(theme, CHAIN_STATES.get(str(state), "pending"))


def log_color(theme, level):
    """日志行的级别色（error / warn）。其它级别由调用方按项目色处理。"""
    return status_color(theme, "log_" + str(level))


def project_color(theme, key):
    """某个项目在指定主题下的身份色。

    表里没有这个项目就返回 None，由调用方决定要不要按名字哈希落到
    fallback 色板（见 project_fallback_color）。
    """
    table = PROJECT_PALETTE.get(normalize_theme(theme), PROJECT_PALETTE[DEFAULT_THEME])
    return table.get(str(key))


def project_fallback_color(theme, key):
    """注册表里没登记的项目：按名字哈希从当前主题的备用色板里取一个。"""
    table = PROJECT_FALLBACK_PALETTE.get(
        normalize_theme(theme), PROJECT_FALLBACK_PALETTE[DEFAULT_THEME]
    )
    return table[sum(ord(ch) for ch in str(key)) % len(table)]


def is_default_project_color(key, value):
    """这个「用户自定义色」是不是其实就是深色主题的出厂默认值。

    settings.yaml 的 gui.project_colors 里一直写着 8 个深色默认色（出厂就有），
    如果把它当作用户自定义直接覆盖主题色板，浅色主题下就永远是那批亮色——
    而用户在设置里根本没动过。等于默认值的项按「未自定义」处理，走主题色板。
    """
    default = PROJECT_PALETTE[DEFAULT_THEME].get(str(key))
    if default is None:
        return False
    return str(default).strip().lower() == str(value).strip().lower()


# 按钮配色。以前是 gui/modern.py 里的模块常量 _BUTTON_VARIANTS，只够深色用；
# 现在按主题给两套。
BUTTONS = {
    "dark": {
        "default": {
            "bg": "#171d2b", "bg_hover": "#212a3e", "bg_press": "#121723",
            "border": "#263044", "border_hover": "#3a4a69", "text": "#d0daec",
            "ripple": "#85b4ff", "radius": 8.0, "gradient": 0.03,
        },
        "primary": {
            "bg": "#4f86f7", "bg_hover": "#6698ff", "bg_press": "#3b73e6",
            "border": "#4f86f7", "border_hover": "#6698ff", "text": "#ffffff",
            "ripple": "#ffffff", "radius": 8.0, "gradient": 0.06,
        },
        "danger": {
            "bg": "#24161f", "bg_hover": "#321e2b", "bg_press": "#1c1118",
            "border": "#46222d", "border_hover": "#5f2e3d", "text": "#ff92a2",
            "ripple": "#ff92a2", "radius": 8.0, "gradient": 0.0,
        },
        "mini": {
            "bg": "#171d2b", "bg_hover": "#212a3e", "bg_press": "#121723",
            "border": "#263044", "border_hover": "#3a4a69", "text": "#d0daec",
            "ripple": "#85b4ff", "radius": 6.5, "gradient": 0.0,
        },
        "parallel": {
            "bg": "#18243b", "bg_hover": "#223354", "bg_press": "#141e33",
            "border": "#2c4673", "border_hover": "#416499", "text": "#93c2ff",
            "ripple": "#93c2ff", "radius": 6.5, "gradient": 0.0,
        },
    },
    "light": {
        "default": {
            "bg": "#ffffff", "bg_hover": "#f3f7fd", "bg_press": "#e8effa",
            "border": "#d8e0ec", "border_hover": "#bcc7da", "text": "#334155",
            "ripple": "#93c5fd", "radius": 8.0, "gradient": 0.02,
        },
        "primary": {
            "bg": "#2563eb", "bg_hover": "#3b82f6", "bg_press": "#1d4ed8",
            "border": "#2563eb", "border_hover": "#3b82f6", "text": "#ffffff",
            "ripple": "#ffffff", "radius": 8.0, "gradient": 0.05,
        },
        "danger": {
            "bg": "#fef3f2", "bg_hover": "#fee4e2", "bg_press": "#fecdca",
            "border": "#fecdca", "border_hover": "#fda29b", "text": "#d92d20",
            "ripple": "#f97066", "radius": 8.0, "gradient": 0.0,
        },
        "mini": {
            "bg": "#ffffff", "bg_hover": "#f3f7fd", "bg_press": "#e8effa",
            "border": "#d8e0ec", "border_hover": "#bcc7da", "text": "#334155",
            "ripple": "#93c5fd", "radius": 6.5, "gradient": 0.0,
        },
        "parallel": {
            "bg": "#ebf3ff", "bg_hover": "#dceaff", "bg_press": "#ccdefc",
            "border": "#b8d2f8", "border_hover": "#8eb7f0", "text": "#1d4ed8",
            "ripple": "#93c5fd", "radius": 6.5, "gradient": 0.0,
        },
    },
}


# 预计算的派生色板缓存：避免 300ms 轮询与万行日志渲染重复做字符串归一与字典推导
_CHAIN_COLOR_TABLES = {
    t_name: {c_key: STATUS_PALETTE[t_name][s_key] for c_key, s_key in CHAIN_STATES.items()}
    for t_name in THEMES
}
_LEVEL_COLOR_TABLES = {
    t_name: {
        "error": STATUS_PALETTE[t_name]["log_error"],
        "warn": STATUS_PALETTE[t_name]["log_warn"],
    }
    for t_name in THEMES
}


def chain_color_table(theme):
    """按主题返回预计算的链状态色字典（零分配只读复用）。"""
    return _CHAIN_COLOR_TABLES.get(normalize_theme(theme), _CHAIN_COLOR_TABLES[DEFAULT_THEME])


def level_color_table(theme):
    """按主题返回预计算的日志级别色字典（零分配只读复用）。"""
    return _LEVEL_COLOR_TABLES.get(normalize_theme(theme), _LEVEL_COLOR_TABLES[DEFAULT_THEME])


def resolve(theme):
    """取主题令牌表；未知主题回退深色（不要让界面因为写错主题名起不来）。"""
    return THEMES.get(str(theme or "").strip().lower(), DARK)


def normalize_theme(theme):
    """把任意输入归一成合法主题名。"""
    name = str(theme or "").strip().lower()
    return name if name in THEMES else DEFAULT_THEME


def button_variants(theme):
    return BUTTONS.get(normalize_theme(theme), BUTTONS[DEFAULT_THEME])


def icon_color(theme=None, subtle=False):
    """单色小图标（拖动把手、关闭、打开仓库…）该用的颜色。

    这些图标是 QPixmap 位图，QSS 里的 `$nav_text` / `$text_muted` 换不到它们
    身上，只能按主题取。subtle=True 取更淡的那一档（次要按钮上的图标）。
    """
    tokens = resolve(normalize_theme(theme))
    key = "icon_subtle" if subtle else "icon"
    return tokens.get(key) or tokens.get("nav_text") or tokens.get("text")


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
