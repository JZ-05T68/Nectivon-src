# E-1 Visual Artifact Index — Prototype（FAIL-018/FAIL-016 修复移交包）

> 历史记录说明（2026-10-15）：下文保留最初交接时的状态与实施计划。
> 视觉索引现已集成到 `src`，数据库继续进行增量迁移。实验回归验证当前
> `SCHEMA_VERSION` 的迁移记录及原有 FTS5 视觉索引表，不再固定为版本 15。

状态：**PROTOTYPE（未接入生产）**。生产树保持 df71ffd 状态（schema 14，全部测试绿）。
本目录是给 Zhao 3B 的 E-1 实现移交包：核心模块已写好并自测通过（除两例按目标态编写的用例），按下面计划接线即可。

## 文件

- `visual_artifact_index.py` = 原型核心模块（与 `src/ai/visual_artifact_index.py` 同一份拷贝）。
  独立、无副作用：惰性建表（`CREATE VIRTUAL TABLE IF NOT EXISTS`）、幂等 rebuild、
  新鲜度跳过（source_text_sha256 比对）、normalize（`2000h`→`2000 小时`、`℃`→`°c`）。
- `test_visual_artifact_index.py` = 针对**接线后目标态**的用例（normalize/rebuild/freshness/
  merge 已在本机验证；`test_rebuild_search_and_freshness` 与 `test_adapter_pool_merge...`
  两例需接线后才能通过——它们引用 `SCHEMA_VERSION==15` 与 `page_visual.merge_visual_hits`）。

## 实施计划（Zhao 3B 按序执行）

1. **migrations.py**：`SCHEMA_VERSION = 15`；新增 `_apply_version_fifteen`
   （仅 `CREATE VIRTUAL TABLE IF NOT EXISTS page_visual_index USING fts5(visual_text, page_id UNINDEXED)`
   + version 记录，纯 additive）；注册 `if current_version < 15`。
   **同步全部 schema pin 测试**（`tests/test_agent_reliability.py:433`、
   `tests/test_schema5_notes.py:203` 等——本次试验实测 14→15 会引发 pin 连锁，
   需逐一同步；这正是当晚停止生产落地的原因）。
2. **page_visual.py**：`PageVisualAdapter.__init__` 加 `visual_index=None` 参数；
   在词法池取得后、`select_family_expanded` 之前：
   `pool = merge_visual_hits(pool, self._visual_index.search_hits(query))`（bounded ≤4，additive-only）。
   合并函数原型已验证：dedupe by (document_id, page_id)，不改词法池顺序。
3. **bootstrap.py**：`build_phase1_handlers` 加 `agent_readings_store=None`；
   vision 分支内 `VisualArtifactIndex(database, store).rebuild()`（93 页毫秒级、幂等）后传入 adapter；
   任何异常 → `visual_index=None` → 行为与现状完全一致。
4. **local_document.py**：`build_phase1_handlers(..., agent_readings_store=readings)`。
5. **backfill**：staging 无需手工 backfill——bootstrap 构建即重建；
   记录 before/after（pages_total=93、pages_indexed、skipped_stale）。
6. **验证闸门**（全部冻结集已备好）：
   `fail018_before_v1.json`（18 例 BEFORE 已跑：**7/18=38.9%，generic 静默旧值 3/3**，
   run_F018_BEFORE_20260906_210321）→ AFTER 同集重跑
   → `fail018_holdout_v1.json`（22 例自然口语盲集，**修复前已冻结，禁止先看先跑**）
   → version_family_v1 / precision 负控 / cc_holdout_v1 回归。

## Authority Boundary（不可违背）

key_facts/keywords/summary 只是 **retrieval aid**：只帮"找到页"，答案仍必须读真实页
（文本层或视觉），citation 指向原页。禁止把 key_fact 当引用证据。
