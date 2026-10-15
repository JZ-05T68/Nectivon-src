# Nectivon v0.8.7 源码同步核对记录

日期：2026-10-15。目标：`JZ-05T68/Nectivon-src` 的 `main` 分支。
本次同步源码，不创建新版本标签或安装包。

## 核对方法与范围

- 从 GitHub API 取得当前分支、提交及完整递归文件树，确认响应没有截断。
- 对当前工作区的 Git 文件清单及源码目录进行独立遍历，包括新增文件与被忽略的文件；
  使用 Git blob 哈希处理仓库约定的换行转换，并以 SHA-256 核对复制前后的原始文件。
- 核对 755 个现有可发布源文件，并加入本记录，共 756 个本地文件。
  保留远端独有的 4 个空目录占位文件及 `docs/v0.8.7-starmap-controls.md`，
  完整发布树应有 761 个文件。
- 发布前检查凭据格式、实际本地凭据匹配和禁止发布的文件类型。
  4 处原有测试假密钥已核实为未改变的合成值；模型资源内的随机编码匹配已核对为
  与远端逐字节相同的 Base64 资源内容。
- 发布后再次读取 GitHub 完整文件树，逐路径、文件模式和 blob 哈希验证其与本地提交相同。
  桌面压缩包仅从该提交的文件清单生成，并逐文件校验内容及 ZIP CRC。
  Windows 启动脚本按 `.gitattributes` 约定使用 CRLF，无 BOM；上游资源保留原始字节。

## 同步的更新

现有业务与回归更新：

- `src/knowledge_object_ui.py`：关系按钮标识包含知识对象 ID，避免重复控件标识。
- `src/workspace_ui.py`：在工具导航中显示已有的 AI 台账入口。
- `tests/test_knowledge_pages_ui.py`、`tests/test_navigation_ui.py`、
  `tests/test_tools_navigation.py`：上述行为的现有回归覆盖。

补齐此前未收录的源码：

- `experiments/e1_visual_index_prototype/`：原型模块、说明与目标态回归。
- `experiments/streamlit_image_coordinates_compat/`：框选坐标兼容实验、合成图片与测试。
- `tests_manual/v086_r1_retest_apptest.py`：使用临时资料的手工回归脚本。
- `docs/v0.8.7-cross-nebula-relations.md`、`docs/v0.8.7-multisubject-maintenance.md`、
  `docs/v0.8.7-starmap-categories-maintenance.md`、`docs/v0.8.7-voice-input-buttons.md`。

本轮检查还修正了实验测试中已经过时的 schema 15 固定断言：现在核对当前迁移版本和
原有 FTS5 视觉索引表，保留检索、新鲜度及幂等断言；在原型 README 中标明历史语境。
同时清除 `tests/test_math_formatting_service.py` 末尾的多余空行，消除差异空白检查错误。
本轮没有修改应用业务逻辑。

## 实际执行的验证

使用现有项目 Python 3.11 环境。保留各命令原始退出码后再读取日志。

| 检查 | 结果 |
| --- | --- |
| 工作区与发布副本：`python -m ruff check .` | 通过，`RUFF_EXIT=0` |
| 工作区：`python -m pytest --tb=short` | 4713 passed, 8 skipped, 1 warning in 2507.20s (0:41:47)，`PYTEST_EXIT=0` |
| 工作区与发布副本：`python -m pytest experiments --tb=short` | 各 22 passed，`PYTEST_EXIT=0` |
| 发布副本：下列 5 个测试文件 | 38 passed，`PYTEST_EXIT=0` |
| 工作区：`python -m pytest tests/test_math_formatting_service.py --tb=short` | 8 passed，`PYTEST_EXIT=0` |
| 下列 JavaScript 测试 | 58 passed，退出码 0 |
| 发布暂存区：`git diff --cached --check` | 通过，退出码 0 |

发布副本的相关回归命令：

```powershell
python -m pytest tests/test_knowledge_pages_ui.py tests/test_navigation_ui.py tests/test_tools_navigation.py tests/test_math_formatting_service.py tests/test_training_profile_public_catalogs.py --tb=short
```

JavaScript 命令：

```powershell
node --test tests/js/knowledge_starmap.test.cjs tests/js/question_crop_editor.test.cjs tests/js/question_image_editor.test.cjs tests/js/starmap_relation_commands.test.cjs tests/js/starmap_selection.test.cjs tests/js/voice_input.test.cjs
```

## 本地数据边界与已知限制

真实 `.env`、凭据、用户文档、原始 PDF、页面图片、数据库、日志、运行状态、备份、
虚拟环境与缓存不进入 GitHub 或源码包。104 份内部/历史审计文档保留在本地。

以下文件按现有 `.gitignore` 明确排除，源码目录的独立核对没有发现其他未解释的遗漏：

- `src/data/schools.json`
- `src/data/undergraduate_majors.json`
- `src/data/training_profile_catalog_sources.json`
- `scripts/apply_stage4_review_fixes.py`
- `scripts/audit_stage4_review.py`
- `scripts/verify_stage4_first_pass.py`
- `scripts/seed_law_foundation_corpus_v1.py`

院校与专业目录的再分发依据尚未确认，公开版不附带这些数据；应用保留已有的目录缺失提示。
本机全量测试使用本地现有目录数据，其结果不代表公开版在缺少这些输入时可运行同样的全量测试。
发布副本的 38 项相关回归和 22 项实验回归均在没有这些目录数据的情况下执行。

全量测试保留一条已有的 Starlette/httpx 弃用警告；本次未调整依赖。未发现未解决的测试失败。
