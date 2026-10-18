#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
compute_signals.py — A股资金管理风险四维信号计算

输入：财务数据 CSV（列名约定见 references/signal-definitions.md，数值统一为亿元）
输出：每家公司四维得分、综合评分、风险等级、触发信号清单

用法：
  python3 compute_signals.py <input.csv> <output.csv> --year <报告年份> [--tier default|strict|loose] [--keep-finance]

--year 为目标年报年度（如 2024）；同比用上一年度数据，需输入中包含上一年度行。
--tier 阈值档位：default / strict / loose，口径见 references/signal-definitions.md。
--keep-finance 保留金融行业（默认排除银行/证券/保险/多元金融）。
"""
import argparse
import math
import sys

import pandas as pd

# ---------------------------------------------------------------- 列名映射
COL_MAP = {
    "code": ["code", "证券代码", "代码"],
    "name": ["name", "证券简称", "简称", "名称"],
    "industry": ["industry", "所属行业", "行业"],
    "report_date": ["report_date", "财报截止日期", "报告期", "截止日期"],
    "report_type": ["report_type", "报告类型"],
    "monetary_funds": ["monetary_funds", "货币资金"],
    "short_loan": ["short_loan", "短期借款"],
    "long_loan": ["long_loan", "长期借款"],
    "bonds_payable": ["bonds_payable", "应付债券"],
    "noncurrent_1y": ["noncurrent_1y", "一年内到期的非流动负债"],
    "finance_expense": ["finance_expense", "财务费用"],
    "accounts_receivable": ["accounts_receivable", "应收账款"],
    "inventory": ["inventory", "存货"],
    "net_profit": ["net_profit", "净利润"],
    "op_cashflow": ["op_cashflow", "经营活动现金流净额"],
    "total_assets": ["total_assets", "总资产", "资产合计"],
    "revenue": ["revenue", "营业收入"],
    "other_receivables": ["other_receivables", "其他应收款"],
    "interest_income": ["interest_income", "利息收入"],
    "hard_evidence": ["hard_evidence", "实锤标记", "hard_evidence_flag"],
}

FIN_KEYWORDS = ("银行", "证券", "保险", "多元金融", "金融")


def resolve_columns(df):
    """把输入 DataFrame 的中英文列名统一映射为内部字段名。"""
    mapping = {}
    for field, aliases in COL_MAP.items():
        for col in df.columns:
            if str(col).strip() in aliases:
                mapping[field] = col
                break
    return mapping


def parse_yi(v):
    """解析为亿元数值。字符串支持 '543.43亿' / '1,897.42万' / '-14.70亿'；数值视为已是亿元。"""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, str):
        s = v.replace(",", "").strip()
        if s in ("", "-", "--", "None", "null", "nan", "NaN"):
            return None
        sign = -1.0 if s.startswith("-") else 1.0
        s = s.lstrip("-").strip()
        mult = 1.0
        if "亿" in s:
            mult = 1.0
            s = s.replace("亿", "")
        elif "万" in s:
            mult = 1e-4
            s = s.replace("万", "")
        s = s.replace("元", "").strip()
        try:
            return sign * float(s) * mult
        except ValueError:
            return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def growth(cur, prev):
    """同比增速（比率）。prev 为 0 或缺失时返回 None。"""
    if cur is None or prev is None or prev == 0:
        return None
    return (cur - prev) / abs(prev)


class Tier:
    def __init__(self, name):
        t = {
            "default": {
                "d1_cash": 0.20, "d1_debt": 0.20, "d1_ratio": 0.50, "d1_yield": 0.015,
                "d2_cf": 0.5, "d2_cum": 0.5,
                "d3_other": 0.05, "d3_other_yoy": 0.50, "d3_yoy2": 1.00,
                "d4_pp": 0.50, "d4_ar_share": 0.15, "d4_inv_yoy": 0.50, "d4_inv_share": 0.20, "d4_rate": 0.02,
            },
            "strict": {
                "d1_cash": 0.15, "d1_debt": 0.15, "d1_ratio": 0.40, "d1_yield": 0.01,
                "d2_cf": 0.3, "d2_cum": 0.5,
                "d3_other": 0.03, "d3_other_yoy": 0.50, "d3_yoy2": 1.00,
                "d4_pp": 0.30, "d4_ar_share": 0.10, "d4_inv_yoy": 0.30, "d4_inv_share": 0.20, "d4_rate": 0.01,
            },
            "loose": {
                "d1_cash": 0.25, "d1_debt": 0.25, "d1_ratio": 0.60, "d1_yield": 0.02,
                "d2_cf": 0.7, "d2_cum": 0.5,
                "d3_other": 0.08, "d3_other_yoy": 0.50, "d3_yoy2": 1.00,
                "d4_pp": 0.80, "d4_ar_share": 0.20, "d4_inv_yoy": 0.80, "d4_inv_share": 0.20, "d4_rate": 0.04,
            },
        }[name]
        self.__dict__.update(t)


def compute_signals(cur, prev, prev2, tier, is_finance):
    """对单家公司（目标年度行 cur、上一年度行 prev、再前一年 prev2）计算四维信号。

    返回 dict：d1..d4 得分、total、risk、signals、coverage、hard_evidence。
    """
    sig = []
    cov = []

    def g(field):
        return parse_yi(cur.get(field)) if field in cur else None

    mf = g("monetary_funds")
    sl = g("short_loan")
    ll = g("long_loan")
    bp = g("bonds_payable")
    nc = g("noncurrent_1y")
    fe = g("finance_expense")
    ar = g("accounts_receivable")
    inv = g("inventory")
    np_ = g("net_profit")
    cf = g("op_cashflow")
    ta = g("total_assets")
    rev = g("revenue")
    orr = g("other_receivables")
    ii = g("interest_income")
    hard = parse_yi(cur.get("hard_evidence")) if "hard_evidence" in cur else None

    def pv(field):
        return parse_yi(prev.get(field)) if prev is not None and field in prev else None

    mf_p, sl_p, ll_p, bp_p, nc_p = pv("monetary_funds"), pv("short_loan"), pv("long_loan"), pv("bonds_payable"), pv("noncurrent_1y")
    fe_p = pv("finance_expense")
    ar_p, inv_p = pv("accounts_receivable"), pv("inventory")
    np_p, cf_p = pv("net_profit"), pv("op_cashflow")
    ta_p, rev_p = pv("total_assets"), pv("revenue")
    orr_p = pv("other_receivables")

    pv2 = (lambda f: parse_yi(prev2.get(f)) if prev2 is not None and f in prev2 else None)
    np_p2, cf_p2 = pv2("net_profit"), pv2("op_cashflow")

    interest_debt = None
    if any(x is not None for x in (sl, ll, bp, nc)):
        interest_debt = sum(x or 0 for x in (sl, ll, bp, nc))

    # ------------------------------------------------ 维度 1 存贷双高（25）
    d1 = 0
    d1_hits = []
    if ta:
        if mf is not None:
            cash_share = mf / ta
            if cash_share >= tier.d1_cash:
                d1_hits.append("a")
        else:
            cov.append("d1:货币资金缺失")
        if interest_debt is not None:
            debt_share = interest_debt / ta
            if debt_share >= tier.d1_debt:
                d1_hits.append("b")
            if mf and interest_debt / mf >= tier.d1_ratio:
                d1_hits.append("c")
        else:
            cov.append("d1:有息负债科目缺失")
        abc = [h for h in d1_hits if h in ("a", "b", "c")]
        d1 = len(abc) * 8
        if abc:
            if len(abc) == 3:
                sig.append("存贷双高信号(子指标:%s)" % ",".join(sorted(abc)))
            elif len(abc) == 2:
                sig.append("存贷双高部分信号(子指标:%s)" % ",".join(sorted(abc)))
            else:
                sig.append("存贷双高弱信号(仅%s，单独不构成存贷双高)" % ",".join(sorted(abc)))
        # d 交叉检验：利息收益率（利息收入缺失时仅当财务费用为净利息收入<0 才可用 -fe 近似；财务费用为正时无法估计，跳过）
        interest_est = ii if ii is not None else (max(0.0, -fe) if (fe is not None and fe < 0) else None)
        if mf and mf > 0 and interest_est is not None:
            yield_rate = interest_est / mf
            if yield_rate < tier.d1_yield:
                d1 = min(25, d1 + 5)
                sig.append("利息收益率过低(%.2f%%，货币资金真实性存疑)" % (yield_rate * 100))
        else:
            cov.append("d1:利息收入缺失，交叉检验未判")
    else:
        cov.append("d1:总资产缺失")

    # ------------------------------------------------ 维度 2 现金流与利润背离（25）
    d2 = 0
    if cf is not None and np_ is not None and ta:
        if np_ > 0 and cf / np_ < tier.d2_cf and prev is not None and np_p is not None and np_p > 0 and cf_p is not None and cf_p / np_p < tier.d2_cf:
            d2 += 12
            sig.append("经营现金流/净利润连续两年<%.1f" % tier.d2_cf)
        elif np_ > 0 and cf / np_ < tier.d2_cf:
            cov.append("d2:仅一年数据，未达连续两年")
        if np_ > 0 and cf < 0 and prev is not None and np_p is not None and np_p > 0 and cf_p is not None and cf_p < 0:
            d2 += 13
            sig.append("净利润为正但经营现金流连续两年为负")
        if np_p2 is not None and cf_p2 is not None and np_ is not None and cf is not None and np_p is not None and cf_p is not None:
            cum_cf = cf + cf_p + cf_p2
            cum_np = np_ + np_p + np_p2
            if cum_np != 0 and cum_cf / cum_np < tier.d2_cum:
                d2 += 8
                sig.append("近三年累计经营现金流/累计净利润<%.1f" % tier.d2_cum)
        elif prev is None or np_p2 is None or cf_p2 is None:
            cov.append("d2:缺三年数据，累计口径未算")
    else:
        cov.append("d2:净利润/现金流/总资产缺失")

    # ------------------------------------------------ 维度 3 资金占用/违规担保（25）
    d3 = 0
    d3_hits = []
    if ta and orr is not None:
        if orr / ta >= tier.d3_other:
            orr_yoy = growth(orr, orr_p)
            if orr_yoy is not None and orr_yoy > tier.d3_other_yoy:
                d3 += 8
                d3_hits.append("a")
                sig.append("其他应收款/总资产=%.1f%%且同比+%.0f%%" % (orr / ta * 100, orr_yoy * 100))
        if orr_p is not None and orr_p > 0:
            orr_yoy = growth(orr, orr_p)
            orr_p2 = pv2("other_receivables")
            orr_yoy_p = growth(orr_p, orr_p2)
            if orr_yoy is not None and orr_yoy > tier.d3_yoy2 and orr_yoy_p is not None and orr_yoy_p > tier.d3_yoy2:
                d3 += 8
                d3_hits.append("b")
                sig.append("其他应收款连续两年增速>100%")
        if mf is not None and mf / ta > 0.20 and orr / ta > 0.05:
            d3 += 4
            d3_hits.append("c")
            sig.append("货币资金与高额其他应收款并存(占用路径)")
    elif orr is None:
        cov.append("d3:其他应收款缺失，仅按实锤计分")

    hard_flag = 0
    if hard and hard == 1:
        d3 = max(d3, 25)
        hard_flag = 1
        sig.append("实锤:资金占用/违规担保/审计监管证据(hard_evidence)")

    # ------------------------------------------------ 维度 4 应收/存货/财务费用异常（25）
    d4 = 0
    ar_share_cur = (ar / ta) if (ar is not None and ta) else None
    ar_share_prev = (ar_p / ta_p) if (ar_p is not None and ta_p) else None
    if ar is not None and rev is not None and ta:
        ar_yoy = growth(ar, ar_p)
        rev_yoy = growth(rev, rev_p)
        if ar_yoy is not None and rev_yoy is not None:
            if (rev_yoy < 0 and ar_yoy > 0) or (ar_yoy - rev_yoy >= tier.d4_pp):
                d4 += 7
                sig.append("应收增速(+%.0f%%)超营收增速(+%.0f%%)≥%.0fpp" % (ar_yoy * 100, rev_yoy * 100, tier.d4_pp * 100))
        if ar_share_cur is not None and ar_share_cur > tier.d4_ar_share and ar_share_prev is not None and ar_share_cur > ar_share_prev:
            d4 += 7
            sig.append("应收账款/总资产=%.1f%%且占比上升" % (ar_share_cur * 100))
        elif ar_share_cur is not None and ar_share_cur > tier.d4_ar_share and ar_share_prev is None:
            cov.append("d4:缺上年占比，占比上升未判")
    elif ar is None or rev is None:
        cov.append("d4:应收/营收缺失")

    if inv is not None and ta:
        inv_yoy = growth(inv, inv_p)
        if inv_yoy is not None and inv_yoy >= tier.d4_inv_yoy and inv / ta > tier.d4_inv_share:
            d4 += 6
            sig.append("存货同比+%.0f%%且/总资产=%.1f%%" % (inv_yoy * 100, inv / ta * 100))
        elif inv_yoy is None:
            cov.append("d4:缺上年存货，存货增速未判")

    if fe is not None and interest_debt is not None and interest_debt > 0:
        if fe > 0 and fe / interest_debt < tier.d4_rate:
            d4 += 5
            sig.append("财务费用/有息负债=%.2f%%(<%.0f%%)，利息支出与负债规模不匹配" % (fe / interest_debt * 100, tier.d4_rate * 100))
        if mf is not None and ta and mf / ta > 0.20 and fe > 0:
            sig.append("货币资金充裕但财务费用仍为净支出")

    d1 = min(25, d1)
    d2 = min(25, d2)
    d3 = min(25, d3)
    d4 = min(25, d4)
    total = d1 + d2 + d3 + d4

    if is_finance:
        risk = "排除-金融行业"
    else:
        if hard_flag:
            risk = "高(实锤)"
        elif total >= tier.high:
            risk = "高"
        elif total >= tier.mid:
            risk = "中"
        else:
            risk = "低"

    return {
        "d1_score": d1, "d2_score": d2, "d3_score": d3, "d4_score": d4,
        "total_score": total, "risk_level": risk,
        "signals": "; ".join(sig),
        "coverage": ("; ".join(sorted(set(cov))) if cov else "完整"),
        "hard_evidence": hard_flag,
        "mf": mf, "interest_debt": interest_debt, "ta": ta,
    }


def main():
    ap = argparse.ArgumentParser(description="A股资金管理风险四维信号计算")
    ap.add_argument("input", help="财务数据 CSV（亿元）")
    ap.add_argument("output", help="输出 CSV 路径")
    ap.add_argument("--year", type=int, required=True, help="目标年报年度，如 2024")
    ap.add_argument("--tier", choices=["default", "strict", "loose"], default="default")
    ap.add_argument("--keep-finance", action="store_true", help="保留金融行业（默认排除）")
    args = ap.parse_args()

    df = pd.read_csv(args.input, dtype=str)
    if df.empty:
        sys.exit("输入 CSV 为空")
    cols = resolve_columns(df)
    missing = [f for f in ("code", "report_date", "monetary_funds", "total_assets") if f not in cols]
    if missing:
        sys.exit("缺少必要列: %s（支持中文列名，见 signal-definitions.md）" % ", ".join(missing))

    data = df.rename(columns={v: k for k, v in cols.items() if v is not None}).copy()
    data["_year"] = data["report_date"].astype(str).str[:4].str.replace("-", "", regex=False)
    data["_year"] = pd.to_numeric(data["_year"], errors="coerce")

    tier = Tier(args.tier)
    tier.high, tier.mid = {"default": (70, 40), "strict": (60, 30), "loose": (80, 50)}[args.tier]

    rows = []
    for code, grp in data.groupby("code"):
        grp = grp.sort_values("_year", ascending=False)
        cur = grp[grp["_year"] == args.year]
        prev = grp[grp["_year"] == args.year - 1]
        prev2 = grp[grp["_year"] == args.year - 2]
        if cur.empty:
            continue
        cur = cur.iloc[0].to_dict()
        prev = prev.iloc[0].to_dict() if not prev.empty else None
        prev2 = prev2.iloc[0].to_dict() if not prev2.empty else None

        industry = str(cur.get("industry", "") or "")
        is_finance = (not args.keep_finance) and any(k in industry for k in FIN_KEYWORDS)

        r = compute_signals(cur, prev, prev2, tier, is_finance)
        row = {
            "code": cur.get("code"),
            "name": cur.get("name", ""),
            "industry": industry,
            "year": args.year,
            "report_type": cur.get("report_type", ""),
            "monetary_funds_yi": r["mf"],
            "interest_debt_yi": r["interest_debt"],
            "total_assets_yi": r["ta"],
            "d1": r["d1_score"], "d2": r["d2_score"], "d3": r["d3_score"], "d4": r["d4_score"],
            "total_score": r["total_score"],
            "risk_level": r["risk_level"],
            "signals": r["signals"],
            "coverage": r["coverage"],
            "hard_evidence": r["hard_evidence"],
        }
        rows.append(row)

    out = pd.DataFrame(rows)
    out["excluded"] = out["risk_level"].str.contains("排除").astype(int)
    out = out.sort_values(["excluded", "risk_level", "total_score"], ascending=[True, False, False])
    out.to_csv(args.output, index=False, encoding="utf-8-sig")

    scored = out[out["excluded"] == 0]
    print("输入公司数(有目标年度数据): %d，其中金融行业排除: %d" % (len(out), int(out["excluded"].sum())))
    if not scored.empty:
        print("风险分布(默认档 高≥%d 中≥%d): 高=%d 中=%d 低=%d" % (
            tier.high, tier.mid,
            (scored["risk_level"].isin(["高", "高(实锤)"]).sum()),
            (scored["risk_level"] == "中").sum(),
            (scored["risk_level"] == "低").sum()))
        top = scored.head(10)[["code", "name", "industry", "total_score", "risk_level", "signals"]]
        print("\nTop 10 高风险公司：")
        print(top.to_string(index=False))
    print("\n输出文件: %s" % args.output)


if __name__ == "__main__":
    main()
