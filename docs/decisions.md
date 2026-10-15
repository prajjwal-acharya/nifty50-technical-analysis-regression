# Project decisions

The source document is a proposed project context, not a confirmed grading brief. The data-processing choices below are frozen for the current data release. Faculty choices about strategy evaluation remain open.

| Decision | Status |
| --- | --- |
| Exact date window | Supplied raw file covers 2016-10-13 through 2026-10-13; used for this processing snapshot. Faculty acceptance of this exact window remains open. |
| Current constituents or historical index membership | Official Nifty 50 list retrieved 2026-10-15 is the frozen universe. BSE replaces WIPRO; all 2,401 BSE observations came from dated NSE bhavcopies. Historical index membership is not claimed. |
| Market data source and treatment of splits/dividends | Raw workbook is preserved. NSE confirms 30 split/bonus factors, seven demerger dates, and eight rights dates, including the previously omitted BEL bonus and two BSE bonuses. Split/bonus-adjusted OHLC is calculated within segments; demergers and rights start new segments. Cash dividends are excluded. Exact original workbook download URL/date remain unknown, but a 15-date, 723-stock-date NSE price spot-check passed. |
| Prediction horizon and stock-return definition | User requested both next-trading-day and next-five-trading-day cumulative adjusted close returns as separate targets on 2026-10-15. Executable next-open returns are kept distinct. |
| Train, validation, and untouched test dates | Pipeline uses train through 2024, validation in 2025, test from 2026-01-01 through 2026-10-13. Freeze before model work. |
| Trading rule, execution timing, costs, and benchmark | Pending faculty/team confirmation |
| Required Excel and Pine Script evidence | Pending evaluator confirmation |

When a choice is made, add the decision, date, reason, and person who confirmed it. Do not revise a frozen choice based on final test results.
