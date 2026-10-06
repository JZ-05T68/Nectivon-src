# Incident Evidence Package — ZBINC-{YYYYMMDD}-{SEQ}

> P0/P1 必填；P2/P3 选填。一次采集到位，不让用户反复复现。
> 敏感内容一律 redact；不记录用户面部；日志只给指针不贴密钥。

```text
INCIDENT_ID        : ZBINC-________-___
USER_TRACK         : A | B | C | D | E     （匿名 ID：U-__）
APP_VERSION        : v0.8.5
ENVIRONMENT        : 8501_formal | 8511_rehearsal
TIMESTAMP          : YYYY-MM-DD HH:MM（GMT+8）
SESSION_ID / TASK  : ZB-_-________-___ / G__ | U__

QUERY              : （用户原问题，redact 后原样保留——不修饰）
VISIBLE_RESPONSE   : （系统当时可见回答要点/原文摘录）
VISIBLE_CITATIONS  : （系统给出的引用：文档/页码/标题）
EXPECTED           : （按资料应是什么）
ACTUAL             : （实际给出什么）
SEVERITY           : P0 | P1 | P2 | P3
USER_IMPACT        : （用户当场反应/可能误导是什么）
REPRO_STATUS       : reproduced_once | observed_once | not_retried（默认不追打用户）
DATA_SCOPE         : （涉及哪台环境/哪些资料；是否触及正式数据）
ERROR_TYPE         : COMMISSION | FABRICATION | SILENT_STALE | VERSION_CONDITION_OBJECT |
                     EVIDENCE | EXPERIENCE_AUTHORITY | HONEST_OMISSION | UI |
                     MENTAL_MODEL | OTHER（主因在前，可加次因）
SCREENSHOT_PATH    : （本地路径，只含产品界面）
LOG_POINTER        : （日志文件+时间窗，不贴内容）
FOLLOW_UP          : （处置动作：通知/停止扩大/修复决策链接）
```

## 处置速查（详见 Incident Runbook）

- P0：立即停止当天测试 → 当场通知项目所有者 → 冻结证据 → 评估 rollback；
- P1：单次可完成会话（先向用户澄清），重复出现即停止扩大 → 当天报告；
- P2：继续，累计 ≥2 或复现即评估停止扩大 → 报告时通知；
- P3：继续，报告时汇总；不必然停止 Pilot。

> 功能请求不是 Incident：另记 FEATURE_REQUEST（feedback.csv feature_requests 列）。
