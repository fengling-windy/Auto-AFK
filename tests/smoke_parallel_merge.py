"""并行块合并（chain.parallel_blocks → 步骤序列）的单元冒烟。

总览卡片上点「并行」写的是 ``chain.parallel_blocks``，supervisor 跑链时由
``merge_parallel_blocks`` 把相邻步骤合成「同时运行」的一步。这段逻辑决定了
用户点出来的组到底会不会真的并行，历史上两处坑都用断言钉住：

1. 三张以上（多项目并行）：默认注册表里三月七 + BAAH 本来就同属
   ``cloud_and_emulator`` 并行组，用户把它俩连起来之后再点第三张，块里的两个
   成员会落在**同一个步骤**里。老实现一看「有两个成员在同一步」就整块作废，
   于是界面上画着三张卡的连线、实际还是按原分组跑。
2. 不能偷人：A-B 已并行时只点 A 与 C，B 不能被顺手拉进这一组。

只算步骤，不启动任何进程：用 ``Supervisor._chain_plan`` 排计划。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from afk_core.events import EventBus
from afk_core.supervisor import Supervisor, merge_parallel_blocks


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name + (("  -- " + str(detail)) if detail and not ok else ""))
    return ok


def _steps(*groups):
    """手搓步骤序列：每组一个 step，members 为组员列表。"""
    return [
        {"group": {"id": "g{0}".format(i)}, "members": list(members), "parallel": len(members) > 1, "merged": False}
        for i, members in enumerate(groups)
    ]


def _shape(steps):
    return [([str(m) for m in s.get("members", [])], bool(s.get("parallel"))) for s in steps]


def _run():
    ok = True

    # -- 1. 与已有并行步骤共享成员（用户最常走的那条路）----------------------
    warns = []
    base = _steps(["A", "B"], ["C"], ["D"])
    got = merge_parallel_blocks(base, [["A", "B", "C"]], warn=warns.append)
    ok &= check("块里含一个已在并行的步骤时仍能合并", _shape(got) == [(["A", "B", "C"], True), (["D"], False)], _shape(got))
    ok &= check("合并成功时不告警", warns == [], warns)
    ok &= check("合并后的一步标了 merged", [s.get("merged") for s in got] == [True, False], [s.get("merged") for s in got])

    # 四张：跨过两个组
    warns = []
    got = merge_parallel_blocks(_steps(["A", "B"], ["C"], ["D"]), [["A", "B", "C", "D"]], warn=warns.append)
    ok &= check("四张也能并成一步", _shape(got) == [(["A", "B", "C", "D"], True)], _shape(got))
    ok &= check("四张合并同样不告警", warns == [], warns)

    # -- 2. 不能偷人：块没覆盖掉原有并行步骤的全部成员 ------------------------
    warns = []
    got = merge_parallel_blocks(_steps(["A", "B"], ["C"]), [["A", "C"]], warn=warns.append)
    ok &= check("块只覆盖并行组的一部分时整块作废", _shape(got) == [(["A", "B"], True), (["C"], False)], _shape(got))
    ok &= check("作废时点名被牵连的项目", any("B" in w for w in warns), warns)

    # -- 3. 不相邻 ------------------------------------------------------------
    warns = []
    got = merge_parallel_blocks(_steps(["A"], ["B"], ["C"], ["D"]), [["A", "C"]], warn=warns.append)
    ok &= check("不相邻的两张不能并行", _shape(got) == [(["A"], False), (["B"], False), (["C"], False), (["D"], False)], _shape(got))
    ok &= check("不相邻时给出「隔着别的项目」的提示", any("隔着别的项目" in w for w in warns), warns)

    # -- 4. 成员不在这次运行里 ------------------------------------------------
    warns = []
    got = merge_parallel_blocks(_steps(["A"], ["B"]), [["A", "Ghost"]], warn=warns.append)
    ok &= check("块里有不在这次运行的项目时整块作废", _shape(got) == [(["A"], False), (["B"], False)], _shape(got))
    ok &= check("缺成员时给出「不在这次运行」的提示", any("不在这次运行" in w for w in warns), warns)

    # -- 5. 退化输入 ----------------------------------------------------------
    warns = []
    base = _steps(["A"], ["B"])
    ok &= check("空块列表原样返回", merge_parallel_blocks(base, []) == base)
    got = merge_parallel_blocks(base, [["A"]], warn=warns.append)
    ok &= check("只有一个成员的块被忽略", _shape(got) == [(["A"], False), (["B"], False)], _shape(got))
    ok &= check("单成员块不告警（用户还没配完）", warns == [], warns)
    got = merge_parallel_blocks(base, [["A", "B"], ["A", "B"]], warn=warns.append)
    ok &= check("重复的块不会把同一步并两次", _shape(got) == [(["A", "B"], True)], _shape(got))

    # -- 6. 走真实注册表：三张 / 四张都能排成一步 ------------------------------
    bus = EventBus()
    try:
        sup = Supervisor(bus)
        registry = sup.registry
        keys = list((registry.get("projects") or {}).keys())
        order = list(keys)
        plan = sup._chain_plan(registry.get("groups") or [], order)
        ok &= check("默认注册表排出的第一步本来就是并行组",
                    any(len(s.get("members") or []) >= 2 and s.get("parallel") for s in plan), _shape(plan))
        first_members = next(s["members"] for s in plan if len(s.get("members") or []) >= 2)
        third = next((k for k in keys if k not in first_members), None)
        if third is not None:
            merged3 = sup._chain_plan(
                registry.get("groups") or [], order,
                parallel_blocks=[first_members + [third]],
            )
            merged_members = [m for s in merged3 if s.get("merged") for m in s["members"]]
            ok &= check("真实注册表里三张能并成一步（{0}）".format(merged3 and [s.get("members") for s in merged3]),
                        set(first_members + [third]) <= set(merged_members))
            merged4_extra = [k for k in keys if k not in (first_members + [third])]
            if merged4_extra:
                merged4 = sup._chain_plan(
                    registry.get("groups") or [], order,
                    parallel_blocks=[first_members + [third, merged4_extra[0]]],
                )
                merged4_members = [m for s in merged4 if s.get("merged") for m in s["members"]]
                ok &= check("真实注册表里四张能并成一步",
                            set(first_members + [third, merged4_extra[0]]) <= set(merged4_members))
            # 链计划里不该混进告警（并行块被作废时会 emit warn 事件）
            queue = bus.subscribe()
            sup._chain_plan(registry.get("groups") or [], order, parallel_blocks=[first_members + [third]])
            events = []
            while True:
                try:
                    events.append(queue.get_nowait())
                except Exception:
                    break
            blocked = [e for e in events if "并行块" in str(e.get("message") or "")]
            bus.unsubscribe(queue)
            ok &= check("三张并行的链计划不产生「并行块已忽略」告警", blocked == [], blocked)
    finally:
        try:
            bus.close_handles()
        except Exception:
            pass

    return ok


def main():
    ok = _run()
    print("PARALLEL MERGE", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
