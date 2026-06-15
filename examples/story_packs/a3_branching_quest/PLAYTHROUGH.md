# 盐契分岔试玩路径

这条路径用于 A3 golden pack 验收，覆盖共同线索、两条互斥路线之一、汇合阶段和结案阶段。

## 守备所路线

1. `检查潮湿告示`
2. `去旧码头`
3. `检查网下盐箱`
4. `去守备所`
5. `向云校尉公开证据`
6. `检查守备案板`
7. `进入封契库`
8. `比对封契缺口`

预期后果：

- 任务 `unmask_the_salt_deal` 从 `gather_leads` 推进到 `choose_approach`。
- 选择守备所路线后进入 `report_to_watch`，并写入 `branch_report_to_watch`。
- 检查守备案板后汇合到 `seal_the_evidence`。
- 比对封契缺口后进入 `case_closed` 且任务完成。

## 旧码头路线

1. `检查潮湿告示`
2. `去旧码头`
3. `检查网下盐箱`
4. `与线人席舟交易`
5. `复查封好的盐箱`
6. `绕去封契库后门`
7. `比对封契缺口`

预期后果：

- 选择旧码头路线后进入 `strike_quay_bargain`，并写入 `branch_strike_quay_bargain`。
- 复查盐箱后同样汇合到 `seal_the_evidence`。
- 结案卷宗必须能回看先前选择，而不是只显示最终 `completed`。
