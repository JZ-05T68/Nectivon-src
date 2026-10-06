# Feedback & Interview Sheet — U-__（Track _，Session ZB-_-________-___）

> 测试结束后 5 分钟反馈 + 必要时 5–10 分钟访谈。匿名；自由文本出现个人信息当场删除。
> 汇总列对应 `feedback_template.csv`。

## 8 题反馈

1. 今天你最想用它解决什么？ → `q1_want_to_solve`
2. 有没有哪一步你不知道该怎么办？当时在做什么？ → `q2_stuck_step`
3. 有没有一次回答让你觉得**不可信**？为什么？ → `q3_untrusted_answer`
4. 有没有一次它说「没找到/证据不足」，但你觉得资料里明明有？ → `q4_miss_but_exists`
5. 你觉得**最有价值**的是哪个部分？ → `q5_most_valuable` / `top_value_feature`
   （检索 / Agent 回答 / 引用出处 / 经验 / 记忆 / 可视化 / 其它）
6. 最没用 / 最难理解的是什么？ → `q6_least_useful`
7. 你会不会再用一次？为什么？ → `q7_return_intent`（yes|unsure|no）+ `q7_reason`
   （注意：这是口头意愿 RETURN_INTENT，与未来 ACTUAL_RETURN 分开统计）
8. 你希望它下一步解决什么？ → `q8_next_request`（同时进 FEATURE_REQUEST 列表）

+ 自由反馈 → `free_text`

## Mental Model 四问（判定 CORRECT / PARTIAL / INCORRECT → `mental_model`）

1. 你觉得这个软件刚才是怎么找到答案的？
2. 它和直接问普通 AI 有什么不同？
3. 如果它说找不到，你认为发生了什么？
4. 你觉得保存的经验和资料有什么区别？

## Teach-back（选做，五项 → `teachback_q1..q5`：correct|partial|wrong|na）

让用户用自己的话解释：①资料从哪里来 ②Agent 怎么回答 ③引用有什么作用
④证据不足时为什么可能不回答 ⑤保存的信息以后可能怎么被再次使用。
记录用户原话摘要（redact）。

## 价值信号（观察到即打标，分号分隔 → `value_signals`）

VS_MORE_MATERIAL / VS_RETURN_QUERY / VS_SAVE_EXPERIENCE / VS_PAST_PAIN / VS_OTHER(+原话)

## 访谈追问（重点用户，非引导式）

- 「你刚才为什么点了 / 没点来源？」
- 「刚才系统说证据不足的时候，你是怎么理解的？」
- 「你觉得它最像什么软件？」
- 「如果明天继续用，你会放什么资料进去？」
- 「今天有没有哪一刻你觉得『这个我以前找起来很麻烦』？」
