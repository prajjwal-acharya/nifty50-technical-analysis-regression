# Project decisions

The source document is a proposed project context, not a confirmed grading brief. The data-processing choices below are frozen for the current data release. The user clarified the forecasting objective on 2026-10-16; see [the price-forecasting plan](price-forecasting-plan.md).

| Decision | Status |
| --- | --- |
| Exact date window | Supplied raw file covers 2016-10-13 through 2026-10-13; used for this processing snapshot. Faculty acceptance of this exact window remains open. |
| Current constituents or historical index membership | Official Nifty 50 list retrieved 2026-10-15 is the frozen universe. BSE replaces WIPRO; all 2,401 BSE observations came from dated NSE bhavcopies. Historical index membership is not claimed. |
| Market data source and treatment of splits/dividends | Raw workbook is preserved. NSE confirms 30 split/bonus factors, seven demerger dates, and eight rights dates, including the previously omitted BEL bonus and two BSE bonuses. Split/bonus-adjusted OHLC is calculated within segments; demergers and rights start new segments. Cash dividends are excluded. Exact original workbook download URL/date remain unknown, but a 15-date, 723-stock-date NSE price spot-check passed. |
| Prediction horizon and target price | Confirmed by user on 2026-10-16: forecast the NSE quoted closing price in rupees for the next trading session and the fifth subsequent trading session. The previous adjusted-return targets remain an archived release component, not the primary result. |
| Primary model-selection metric | Absolute difference between predicted and actual quoted close in rupees, averaged equally across stocks; also report row-weighted rupee MAE and RMSE. The unchanged-price forecast is the baseline. |
| Train, validation, and test dates | Pipeline uses train through 2024, validation in 2025, test from 2026-01-01 through 2026-10-13. The retained 26-feature next-session model and corrected 30-feature fifth-session candidate have both been scored on 2026; that period cannot serve as a blind final holdout for later revisions. |
| Retained model paths | On 2026-10-16 the user chose the next-session and corrected fifth-session models as the only rerunnable model paths. The fifth-session candidate did not pass its 2025 unchanged-price gate. See [model results](../reports/model-results-2026-10-16.md). |
| Trading rule, execution timing, costs, and benchmark | Removed from the current objective per user clarification; this project is about price-forecast error, not buy/sell decisions or profit. |
| Required Excel evidence | Pending evaluator confirmation; no Excel workbook is required to rerun either current model. |

When a choice is made, add the decision, date, reason, and person who confirmed it. Do not revise a frozen choice based on final test results.
