# -*- coding: utf-8 -*-
"""极简试验日志（append-only）。每条记录一个「已登记的口径」，用来防止事后反复改参数直到显著。

用法：
    from _trial_log import record
    record(id="T004", round="R3", desc="...", spec={...}, n_tests=..., result="...", decision="...")
同一 id 重复登记会被跳过（幂等）。
"""
import json, hashlib, os, datetime

P = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "trial_log.json")


def _load():
    if os.path.exists(P):
        return json.load(open(P, encoding="utf-8"))
    return {"note": "append-only；改动口径必须新增条目，不得就地修改历史条目",
            "entries": []}


def record(entry_id, round_, desc, spec, n_tests, result, decision):
    d = _load()
    if any(e["id"] == entry_id for e in d["entries"]):
        return False
    spec_s = json.dumps(spec, ensure_ascii=False, sort_keys=True)
    d["entries"].append({
        "id": entry_id,
        "ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "round": round_,
        "desc": desc,
        "spec_sha1": hashlib.sha1(spec_s.encode("utf-8")).hexdigest()[:12],
        "spec": spec,
        "n_tests": n_tests,
        "result": result,
        "decision": decision,
    })
    os.makedirs(os.path.dirname(P), exist_ok=True)
    json.dump(d, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return True


if __name__ == "__main__":
    HS12 = [5, 10, 20, 40, 60, 90, 120, 180, 250, 375, 500, 750]
    HS18 = [1, 2, 3, 5, 8, 10, 15, 20, 30, 40, 60, 90, 120, 180, 250, 375, 500, 750]
    record(
        "T001", "R1",
        "全样本周期扫描（12 周期网格）",
        dict(horizons=HS12, factors=18, null="circular_shift", n_shift=400, fdr="BH 族内(12)"),
        216,
        "可用尺度 3（综合分/神奇指标/两融）；待验证 4；大效应不可证 6；其余 5",
        "被 T002 取代：用户要求更细的微观尺度，网格扩到 18 周期")
    record(
        "T002", "R1",
        "全样本周期扫描（18 周期网格，加入 1/2/3/8/15/30 日）",
        dict(horizons=HS18, factors=18, null="circular_shift", n_shift=400, fdr="BH 族内(18)"),
        324,
        "可用尺度 2（神奇指标 60-500、两融 500）；待验证 4；综合分 500 日 q 由 0.060 变为 0.135，跌破 0.10 门槛",
        "网格冻结为 v1；记录「族系变大→门槛变严」的直接影响")
    record(
        "T003", "R2",
        "滚动起点：历史段定带 → 后续 4 年验证（三种带定义对照）",
        dict(horizons=HS18, origins="2019-01~2023-07 半年一次", oos_window=1000,
             min_ic=0.15, min_oos=0.10, variants=["edge_band", "core_band", "fixed_180_250_375"]),
        10 * 18 * 3,
        "三种定义的样本外通过率 0.62 / 0.65 / 0.67，无实质差异；"
        "历史数据挑出的「有效带」并不比「固定用长带」更好",
        "放弃「每因子精确有效带」，界面改为粗粒度尺度标签（微观/中/长），保留剖面形状")
    print(json.dumps(_load(), ensure_ascii=False, indent=1)[:600])
