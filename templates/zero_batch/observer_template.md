# Observer Sheet — 单任务观察记录（每任务一份 / 或直接填 observations.csv）

> 填表纪律：FIRST_ACTION 前不给操作指导；只记录不提示；
> 引用原话时删除个人信息；USER_BLOCKED 属 UX/Mental Model，不记成 Agent Failure。

```text
SESSION_ID        : ZB-_-________-___          （tester_session_template.csv 已登记）
USER_TRACK        : A 中学生 | B 一般工科 | C AI/CS | D 工程师 | E 粗心/低耐心
TASK_ID           : G__ | U__
TASK_KIND         : GUIDED | USER_OWNED
START / END       : __:__ / __:__

FIRST_ACTION      : （用户第一个自然动作，原话/原样描述）
FIRST_ACTION_SUCCESS : true | false | na（未观察到）
USER_BLOCKED      : true | false     （系统没错但用户不知道下一步；注明卡点）

QUERY（主问句）   :
EXPECTED          :
ACTUAL            :

SOURCE_OPENED     : true | false | na（Agent 未给引用 = na）
SOURCE_CORRECT    : true | false | na
USER_UNDERSTOOD_SOURCE : yes | partial | no | na

ERROR_TYPE（任务级主归因，可先 NONE 后按记录改判）:
  NONE / COMMISSION / FABRICATION / SILENT_STALE / VERSION_CONDITION_OBJECT /
  EVIDENCE / EXPERIENCE_AUTHORITY / HONEST_OMISSION / HONEST_BOUNDARY /
  USER_BLOCKED / OTHER

RECOVERY_EVENT    : true | false     （采集当时预标记，禁止事后补判）
RECOVERY_SUCCESS  : true | false
USER_JUDGMENT     : （User-Owned 必填：有用/一般/没用 + 一句话）
USER_COMMENT      :
OBSERVER_NOTE     :
SEVERITY          : NONE | P0 | P1 | P2 | P3（非 NONE → 同时建 incident 记录）

TASK_SUCCESS      : success | partial | incomplete（非产品原因中断） | failed | not_run
```

## Agent 问答轮明细（每轮一行 → turns.csv）

| turn_index | query | answer_summary | citations_given | classification | 备注关键旗标 |
|---|---|---|---|---|---|
| 1 |  |  |  |  |  |
| 2 |  |  |  |  |  |

classification 枚举（单选）：
`pass / commission_wrong_fact / commission_wrong_version / commission_wrong_object_binding /
commission_wrong_condition / commission_wrong_evidence / fabrication / silent_stale /
honest_omission_recall_gap / honest_boundary / honest_refusal / honest_meta /
clarification / recovery_success / invalid / other`

布尔旗标（true/false）：commission、fabrication、silent_stale、
version_condition_object_error、evidence_error、experience_authority_error、
honest_omission、out_of_scope（库外问题）、trust_failure、user_blocked、
recovery_event、recovery_success、invalid。

> 判分提醒：诚实拒答 ≠ 错误（HONEST_OMISSION 不算失败也不算 Success 的对立面）；
> COMMISSION = 用户可能被误导的错误陈述；两者绝不合并。
