# doubao-fund-risk-screener

A股上市公司**资金管理风险筛查** Skill：抓取全A股/指定股票池的年报财务数据，按四个维度用大模型筛查并归因有明显资金管理问题的上市公司，输出风险清单、深度分析报告与论文选题素材。

适用于：论文选题、会计/审计/财务舞弊案例研究、暴雷风险初筛。

## 功能

四维信号（各 0~25 分，综合 0~100）：

1. **存贷双高**：货币资金与有息负债同时高企 + 利息收益率交叉检验（康得新式货币资金真实性存疑）
2. **现金流与利润背离**：经营现金流/净利润连续两年 <0.5、三年累计口径
3. **资金占用/违规担保**：其他应收款表内信号 + 公告/审计/监管实锤（命中直接升高危）
4. **应收/存货/财务费用异常**：应收增速超营收、应收占比上升、存货异常、财务费用与负债规模不匹配

输出：风险清单（CSV/Excel）、深度归因报告、论文选题素材（候选题目/案例库/变量设计）。

## 目录结构

```
doubao-fund-risk-screener/
├── SKILL.md                          # Skill 主流程（触发与执行指引）
├── references/
│   └── signal-definitions.md         # 四维信号口径、阈值档位、经典案例参照
└── scripts/
    └── compute_signals.py            # 指标计算脚本（确定性计算）
```

## 安装为豆包 Skill

1. 克隆或下载本仓库；
2. 将整个 `doubao-fund-risk-screener/` 文件夹放入豆包 Skill 目录：`<workspace>/.user_skills/`；
3. 在对话中直接说「用资金管理风险筛查跑一遍」即可触发。

## 运行

```
# 计算信号（财务数据 CSV 列名约定见 references/signal-definitions.md）
python3 scripts/compute_signals.py <财务数据.csv> <输出.csv> --year 2024 [--tier default|strict|loose] [--keep-finance]
```

示例：

```
python3 scripts/compute_signals.py financials.csv result.csv --year 2024
python3 scripts/compute_signals.py financials.csv result.csv --year 2024 --tier strict
```

## 数据来源与限制

- 财务科目与股票池：豆包官方合作金融数据库（seed_finance_lookup），已实测覆盖货币资金、短/长期借款、应付债券、一年内到期非流动负债、财务费用、应收/存货、净利润、经营现金流、总资产、营业收入等年报科目。
- 公告核验（资金占用/违规担保/审计意见/监管函）：上市公司公告检索与解读。
- 东方财富 push2 等数据 API 被 robots.txt 拦截，本工具不依赖、也不绕过该接口。
- 指标异常为**疑似信号**，不等于舞弊认定；深度归因需公告/审计/监管证据支撑。

仅供学术研究与风险初筛使用，不构成投资建议。
