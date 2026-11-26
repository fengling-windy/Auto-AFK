"""弹簧动画与流式卡片网格槽位检测（纯 Python 数学计算，不依赖 Qt）。"""

SPRING_STIFFNESS = 260.0
SPRING_DAMPING = 23.0


def spring_step(pos, vel, target, dt, stiffness=SPRING_STIFFNESS, damping=SPRING_DAMPING):
    """半隐式欧拉法积分单轴二阶弹簧阻尼系统。

    返回 ``(new_pos, new_vel, settled)``；dt 会夹紧到 [0.001, 0.05] 秒，
    避免系统卡顿一帧时数值发散或大跳变。
    """
    dt = max(0.001, min(float(dt), 0.05))
    disp = float(pos) - float(target)
    acc = -float(stiffness) * disp - float(damping) * float(vel)
    new_vel = float(vel) + acc * dt
    new_pos = float(pos) + new_vel * dt
    settled = abs(float(target) - new_pos) < 0.35 and abs(new_vel) < 4.0
    if settled:
        return float(target), 0.0, True
    return new_pos, new_vel, False


def _rect_tuple(rect):
    if isinstance(rect, tuple):
        return (float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3]))
    return (float(rect.x()), float(rect.y()), float(rect.width()), float(rect.height()))


def pick_drag_slot(center_x, center_y, slot_rects, current_slot=None, hysteresis=8.0):
    """按拖动卡片的中心点匹配目标网格槽位索引。

    按视觉行（y 接近视为同一行）分组：先匹配纵坐标最近的行，再在行内找横坐标
    最近的槽位；若在同一行内与 ``current_slot`` 相邻切换，要求跨过两槽位中线
    至少 ``hysteresis`` 像素，避免卡片停在交界处来回抖动。
    """
    if not slot_rects:
        return 0
    rects = [_rect_tuple(r) for r in slot_rects]
    if len(rects) == 1:
        return 0

    rows = []
    for idx, (x, y, w, h) in enumerate(rects):
        cy = y + h * 0.5
        if not rows or abs(y - rows[-1]["y"]) > 12.0:
            rows.append({"y": y, "cys": [cy], "items": [(idx, x + w * 0.5)]})
        else:
            rows[-1]["cys"].append(cy)
            rows[-1]["items"].append((idx, x + w * 0.5))

    best_row = min(rows, key=lambda r: abs(float(center_y) - (sum(r["cys"]) / len(r["cys"]))))
    items = best_row["items"]
    best_idx, _ = min(items, key=lambda pair: abs(float(center_x) - pair[1]))

    if current_slot is not None and best_idx != current_slot:
        row_indices = [pair[0] for pair in items]
        if current_slot in row_indices:
            cur_x = rects[current_slot][0] + rects[current_slot][2] * 0.5
            cand_x = rects[best_idx][0] + rects[best_idx][2] * 0.5
            mid_x = (cur_x + cand_x) * 0.5
            if cand_x > cur_x and float(center_x) < mid_x + float(hysteresis):
                return current_slot
            if cand_x < cur_x and float(center_x) > mid_x - float(hysteresis):
                return current_slot

    return best_idx
