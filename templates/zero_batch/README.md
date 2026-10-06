# templates/zero_batch/ — 第零批记录模板（TRACKED）

模板可进 Git；**真实用户数据不进 Git**（存 `artifacts/real_user_zero_batch/`，已忽略）。
所有模板不得含真实姓名/联系方式/学号/身份证/学校单位。

| 文件 | 用途 | 数据落点 |
|---|---|---|
| `tester_session_template.csv` | SESSION 登记表头（每 session 一行） | `sessions.csv` |
| `observer_template.md` | 单任务人工观察表（现场用） | 抄录进 `observations.csv` |
| `observations_template.csv` | TASK 观察表头（每任务一行） | `observations.csv` |
| `turns_template.csv` | TURN 问答判分表头（每轮一行） | `turns.csv` |
| `incidents_template.csv` | INCIDENT 摘要表头（每事件一行） | `incidents.csv` |
| `incident_template.md` | P0/P1 证据包（每事件一份） | `artifacts/real_user_zero_batch/incidents/ZBINC-*.md` |
| `feedback_template.md` | 反馈/访谈/Teach-back 现场表 | 抄录进 `feedback.csv` |
| `feedback_template.csv` | USER 反馈汇总表头（每用户一行） | `feedback.csv` |

CSV 首行为**表头**，第二行为**取值说明占位行**（竖线分隔枚举）——
复制到数据文件时只保留表头，删除占位行；汇总脚本会校验列名并拒绝占位行值。

schema 与 `docs/zero-batch/EKB_v0.8.4_ZERO_BATCH_METRICS_V1.md` 一一对应；
字段变更需同步：模板表头 + 汇总脚本 `REQUIRED_COLUMNS` + METRICS_V1 文档。
