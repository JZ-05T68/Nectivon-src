# ruff: noqa: E501
"""Memory seeding for DIRTY_LONG_TERM V2 (staging only).

76 new memory entries on top of dirty-long-term-v1 (24), bringing the total
to 100. Contains four longitudinal chains (refine / contradict / supersede
via ``source_entry_id``), a stale experience, wrong raw_qa judgments,
only-worked-once and similar-but-different pollution pairs, condition- and
version-limited experiences, plus assorted fact-bearing records. Session
history is embedded ("第 N 次会话") per the v0.8.3 plan.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

os.environ["EKB_STAGING_INSTANCE"] = "1"

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "reliability_eval"
MANIFEST_V3 = FIXTURE_DIR / "corpus_manifest_dirty_v3.json"
DELETED_SOURCE_TITLE = "热处理炉温记录_2026Q2"


def _settings():
    from src.config import staging_settings

    return staging_settings()


def _database():
    from src.database import Database

    return Database(_settings().database_path)


def _service(database):
    from src.knowledge_memory_service import KnowledgeMemoryService

    return KnowledgeMemoryService(database)


# (key, spec) — key enables chain references; spec mirrors v1's shapes.
EXPERIENCES: list[tuple[str, dict]] = [
    ("A1", {
        "kind": "experience",
        "title": "齿轮箱异振初次排查（第 1 次会话，怀疑齿隙）",
        "content": "遇到的问题：齿轮箱运行时异响伴振动。 处理方式：初步判断齿隙偏大，计划调整啮合。 结果：调整后略有缓解，未根治。",
        "root_cause": "齿隙偏大（初步怀疑，未验证）",
        "lesson": "先记录振动频谱再动手。",
        "root_cause_confirmed": False,
        "context_conditions": "第 1 次会话；基于听音判断",
    }),
    ("A2", {
        "kind": "experience",
        "title": "齿轮箱异振二次结论（第 8 次会话，联轴器对中 0.08mm）",
        "content": "遇到的问题：齿轮箱异振复测仍超标。 处理方式：百分表测联轴器对中，偏差 0.08 mm，重新对中。 结果：振动下降但仍未达标。",
        "root_cause": "联轴器对中不良（部分原因，已修正）",
        "lesson": "对中改善后仍超标说明另有根因。",
        "root_cause_confirmed": False,
        "context_conditions": "第 8 次会话；百分表复测",
    }),
    ("A4", {
        "kind": "experience",
        "title": "齿轮箱异振最终根因（第 29 次会话，地脚基础松动）",
        "content": "遇到的问题：对中改善后振动仍 3.2 mm/s。 处理方式：频谱发现 50Hz 峰，复查地脚螺栓，预紧力矩普遍偏低 40%。 结果：按 46 Nm 复紧后振动 1.9 mm/s。",
        "root_cause": "地脚基础松动（最终根因，复紧验证）",
        "lesson": "50Hz 峰优先查基础与地脚，不要只盯箱体内部。",
        "root_cause_confirmed": True,
        "context_conditions": "第 29 次会话；齿轮箱 2026 试行版验收工况",
    }),
    ("A5", {
        "kind": "experience",
        "title": "齿轮箱加固后达标验证（第 43 次会话）",
        "content": "遇到的问题：复紧后连续跟踪。 处理方式：两周内三次复测。 结果：振动稳定 0.5 mm/s，A 级区。",
        "lesson": "基础类根因处理后要连续跟踪两个班次。",
        "root_cause_confirmed": True,
        "context_conditions": "第 43 次会话；空载与负载各测",
    }),
    ("B1", {
        "kind": "experience",
        "title": "摄像头画面'振荡'实为曝光闪烁（第 12 次会话）",
        "content": "遇到的问题：质检摄像头画面抖动感。 处理方式：排查机械后确认是工频灯曝光闪烁。 结果：改快门同步后消失。注意与电机振动问题区分。",
        "root_cause": "照明频闪与快门不同步",
        "lesson": "画面振荡先查光源频率，不是机械振动。",
        "root_cause_confirmed": True,
        "context_conditions": "第 12 次会话；质检工位照明改造前",
    }),
    ("B2", {
        "kind": "experience",
        "title": "摄像头支架减振土办法（第 20 次会话，仅一次有效）",
        "content": "遇到的问题：cam2 画面轻微晃动。 处理方式：临时加橡胶垫。 结果：那一次有效，后来同样做法无效。",
        "outcome": "only worked once",
        "lesson": "土办法不要当通用方案。",
        "root_cause_confirmed": False,
        "context_conditions": "仅第 20 次会话那次有效",
    }),
    ("C3", {
        "kind": "experience",
        "title": "E17 错误码两系统含义不同（第 31 次会话，条件限定）",
        "content": "遇到的问题：变频器与 PLC 都报 E17，处置完全不同。 处理方式：变频器 E17=散热过温，PLC E17=通讯超时。 结果：按设备类型分别处置。适用范围：仅这两个系统。",
        "lesson": "错误码必须先看设备类型，E17 不是同一个东西。",
        "root_cause_confirmed": True,
        "context_conditions": "仅限本厂变频器与 PLC 两系统",
    }),
    ("D1", {
        "kind": "experience",
        "title": "空压机维护周期 500 小时（第 3 次会话，旧结论）",
        "content": "遇到的问题：空压机保养排期。 处理方式：按当时规程 500 小时执行。 结果：沿用多年。备注：后被高粉尘规定部分取代。",
        "lesson": "按现行规程周期保养。",
        "root_cause_confirmed": True,
        "context_conditions": "第 3 次会话；基于 2019 规程时代，高粉尘车间已被取代",
    }),
    ("D3", {
        "kind": "experience",
        "title": "高粉尘车间空压机周期缩短至 300 小时（第 58 次会话，更新旧结论）",
        "content": "遇到的问题：二号机在高粉尘车间损耗快。 处理方式：按二号机手册第 2 章执行。 结果：高粉尘环境 300 小时，一般环境仍 500 小时。",
        "root_cause": "粉尘浓度决定周期分档",
        "lesson": "周期先看环境分档，旧结论不是全错是条件没分开。",
        "root_cause_confirmed": True,
        "context_conditions": "第 58 次会话；高粉尘车间 >10 mg/m³",
    }),
    ("E1", {
        "kind": "experience",
        "title": "热处理炉温校准周期 6 个月（第 40 次会话，来源已删除）",
        "content": "遇到的问题：炉温定期校准安排。 处理方式：按 2026Q2 炉温记录执行。 结果：校准周期 6 个月，偏差 +3°C 已修正。备注：原记录资料后因归档调整删除，此经验保留。",
        "lesson": "校准记录即使删除，周期结论仍有效。",
        "root_cause_confirmed": True,
        "context_conditions": "第 40 次会话；热处理炉",
    }),
    ("G1", {
        "kind": "experience",
        "title": "电机振荡处理（第 15 次会话，降速度环增益）",
        "content": "遇到的问题：电机高负载振荡。 处理方式：速度环增益从 25Hz 降 10%。 结果：振荡消失。",
        "lesson": "电机振荡先动增益，与摄像头画面振荡是两回事。",
        "root_cause_confirmed": True,
        "context_conditions": "第 15 次会话；调试手册第 6 章流程",
    }),
    ("G2", {
        "kind": "experience",
        "title": "摄像头画面振荡处理（第 16 次会话，快门与曝光）",
        "content": "遇到的问题：画面振荡感。 处理方式：调整快门与曝光同步。 结果：消失。与电机振动处理路径完全不同。",
        "lesson": "画面振荡查光源与快门。",
        "root_cause_confirmed": True,
        "context_conditions": "第 16 次会话",
    }),
    ("G3", {
        "kind": "experience",
        "title": "联轴器机械对中（第 22 次会话，百分表法）",
        "content": "遇到的问题：联轴器对中验收。 处理方式：百分表双表法，径向轴向各测四点。 结果：0.05 mm 以内合格。",
        "lesson": "机械对中用百分表，不是相机标定。",
        "root_cause_confirmed": True,
        "context_conditions": "第 22 次会话",
    }),
    ("G4", {
        "kind": "experience",
        "title": "视觉系统'对中'标定（第 23 次会话，九点标定）",
        "content": "遇到的问题：视觉引导偏差大。 处理方式：九点标定重做。 结果：引导误差回到 0.3 px。注意与机械对中是两件事。",
        "lesson": "视觉对中是标定，不是百分表。",
        "root_cause_confirmed": True,
        "context_conditions": "第 23 次会话",
    }),
    ("H1", {
        "kind": "experience",
        "title": "液压站油压 12 MPa 判读（第 6 次会话，仅 v1 阶段）",
        "content": "遇到的问题：液压站油压表读数判断。 处理方式：按 v1 巡检要点 12 MPa。 结果：当时判定合格。备注：v2 已改 12.5 MPa，本条仅适用 v1 阶段。",
        "lesson": "版本阶段不同额定值不同。",
        "root_cause_confirmed": True,
        "context_conditions": "仅液压站巡检要点 v1 时期",
    }),
    ("H2", {
        "kind": "experience",
        "title": "涂装烘干 140°C 经验（第 9 次会话，参数卡已作废）",
        "content": "遇到的问题：涂装烘干温度设定。 处理方式：按当时参数卡 140°C。 结果：当时合格。备注：参数卡已作废，现行 160°C。",
        "lesson": "旧参数卡作废后结论随之作废。",
        "root_cause_confirmed": True,
        "context_conditions": "仅作废参数卡时期；现行 160°C",
    }),
]

ASSORTED_EXPERIENCES = [
    ("齿轮箱轴承温度报警阈值速查（第 30 次会话）", "报警阈值分版本：2024 版 75°C、2025 修订 78°C、2026 试行 80°C。按现行版执行。", "阈值以现行版为准。", True, "齿轮箱；2026 试行版语境"),
    ("液压油温限值 55°C（第 10 次会话）", "液压站油温不得超过 55°C，趋势图 100h 时 56°C 即超限。", "油温接近 55 就要查冷却。", True, "液压站"),
    ("电机负载电流对照（第 18 次会话）", "50% 负载约 8A，75% 约 11A，100% 约 15A（曲线图）。", "电流对照负载判过载。", True, "380V 四极电机"),
    ("车间噪音最高工位（第 25 次会话）", "工位 D 85 dB 最高，工位 C 75 dB 最低。", "噪音超标先查 D 工位。", False, "各工位噪声普查"),
    ("摄像头帧率速查（第 26 次会话）", "cam4 60fps 最高，cam2 25fps 最低。", "帧率不足先换 cam2 口的线。", True, "质检相机"),
    ("焊接电流选型（第 27 次会话）", "2mm 板 90A、3mm 120A、4mm 150A。", "按板厚选电流。", True, "手工电弧焊"),
    ("注塑周期冷却占大头（第 28 次会话）", "冷却 50%、锁模 20%、射胶 15%、顶出 15%。", "提产能先压冷却段。", True, "注塑成型"),
    ("点检表按车间区分（第 33 次会话）", "冲压车间每日班前；装配车间每周。两份表别混用。", "点检频次看车间。", True, "两车间"),
    ("年度大修次数 2026 为三次（第 34 次会话）", "2025 年两次（8/12 月）；2026 年三次（4/8/12 月）。", "排产按 2026 计划。", True, "2026 年度"),
    ("变频器风扇每月清洁（第 35 次会话）", "散热风扇每月清洁，E17 散热过温优先查风道。", "风扇清洁是月度动作。", True, "变频器"),
    ("PLC 通讯超时排查（第 36 次会话）", "E17 通讯超时先查终端电阻与总线。", "PLC 侧 E17 不是散热问题。", True, "PLC"),
    ("输送线急停每月测试（第 37 次会话）", "急停回路每月一次，复位需两人。", "急停测试雷打不动。", True, "输送线"),
    ("光电对准公差 5mm（第 38 次会话）", "光电传感器对准公差 5 mm。", "对不准先看安装座。", False, "输送线光电"),
    ("链速 0.8 m/s（第 39 次会话）", "输送线链速 0.8 m/s。", "提速需重新评估急停距离。", True, "输送线"),
    ("接地电阻 4Ω（第 41 次会话）", "设备接地 ≤4 Ω，雨季前复测。", "接地年年测。", True, "全厂电气"),
    ("ISO 10816 A 级 0.71（第 42 次会话）", "振动 A 级 ≤0.71 mm/s，C 级以上检修。", "判级用 ISO 速查。", True, "旋转设备"),
    ("6204 内径 20mm（第 44 次会话）", "6204→20mm、6205→25mm、6206→30mm。", "型号末两位乘五。", True, "轴承选型"),
    ("2.2kW 额定 4.7A（第 45 次会话）", "2.2 kW 四极 380V 约 4.7 A。", "电流异常先比额定。", True, "低压电机"),
    ("参数备份每季度（第 46 次会话）", "变频器参数备份每季度，文件名带日期。", "备份不做，坏一台丢一套。", True, "变频器"),
    ("气缸行程 50/100/200（第 47 次会话）", "CQ2 常用 50/100/200 mm。", "选型查样本。", True, "气缸"),
    ("滤芯最低库存 1 件（第 48 次会话）", "滤芯 P-191116 最低 1 件，现 2 件。", "消耗件设最低库存。", True, "备件"),
    ("新皮带挠度 4.5mm（第 49 次会话）", "跨距 1000mm 新带中部挠度 4.5 mm。", "张紧按挠度不按手感。", True, "皮带传动"),
    ("法兰力矩 M12 65（第 50 次会话）", "M12 65 Nm、M16 100 Nm，对角三次拧紧。", "法兰不许一次拧死。", True, "法兰装配"),
    ("培训覆盖 8 人（第 51 次会话）", "2026Q1 新员工安全培训 8 人全过。", "记录留档。", True, "培训"),
    ("伺服增益默认 25Hz（第 52 次会话）", "速度环增益出厂 25 Hz，调整先降后升 10%。", "增益乱调先回默认。", True, "伺服"),
    ("过载 120%/60s（第 53 次会话）", "伺服过载能力 120%/60 秒。", "过载报警先看时间。", True, "伺服"),
    ("报警清除后复测三循环（第 54 次会话）", "伺服报警清除后必须复测三个循环。", "不复测等于没修。", True, "伺服"),
    ("油品更换 2000h（第 55 次会话）", "二号机首次 500h，之后每 2000h。", "换油按小时数。", True, "二号机"),
    ("排气温度 105°C 联锁（第 56 次会话）", "二号机排气 105°C 报警并联锁。", "排气超温别硬扛。", True, "二号机"),
    ("储气罐排污 500h（第 57 次会话）", "二号机储气罐每 500h 排污。", "排污排进保养表。", True, "二号机"),
    ("冷却水进口 ≤35°C（第 59 次会话）", "二号机冷却水进口不超过 35°C。", "水温高先查冷却塔。", True, "二号机"),
    ("U/V 对调仅应急（第 60 次会话）", "相序对调只是应急，禁止当根因处理。", "对调后必须找真根因。", True, "伺服"),
    ("编码器线远离动力线（第 61 次会话）", "编码器线缆与动力线分离敷设。", "干扰从布线防。", False, "伺服"),
    ("位置环=速度环一半起步（第 62 次会话）", "位置环增益按速度环 1/2 起步。", "两环别一次调。", False, "伺服"),
    ("端子紧固每半年（第 63 次会话）", "输送线端子紧固每半年一次。", "松端子是隐患大头。", True, "输送线"),
    ("装配扭矩枪 8Nm（第 64 次会话）", "装配线扭矩枪设定 8 Nm，每周点检。", "扭矩枪周检。", True, "装配车间"),
    ("冲压行程 1.2m（第 65 次会话）", "冲压 A 线滑块行程 1.2 m，气压 0.6 MPa。", "行程气压天天看。", True, "冲压车间"),
    ("2019 规程已废止（第 66 次会话）", "空压机 2019 规程（1000h）已废止，现行 500h。", "废止规程别再翻。", True, "空压机"),
    ("点检频次改为每日（第 67 次会话）", "旧指导书每周点检已被 2026 版每日取代。", "按新版每日执行。", True, "点检"),
    ("烘干现行 160°C（第 68 次会话）", "涂装烘干现行 160°C，旧卡 140°C 作废。", "参数卡看日期。", True, "涂装"),
    ("润滑周期以指导书 1500h 为准（第 69 次会话）", "周期卡 2000h 与指导书 1500h 冲突，书面指导书优先。", "冲突以书面指导为准并上报。", True, "润滑"),
    ("绝缘合格线按分册（第 70 次会话）", "低压在役 ≥1 MΩ；新绕组交付 ≥10 MΩ。", "两个标准用途不同。", True, "电机绝缘"),
    ("炉温偏差 +3°C 已修正（第 71 次会话）", "2026Q2 校准偏差 +3°C，当月修正。", "偏差记录要闭环。", True, "热处理"),
    ("_cam2 最低 25fps（第 72 次会话）", "cam2 25fps 最低，拍快速运动用 cam4 60fps。", "按帧率分配机位。", True, "相机"),
    ("_工位 C 最安静（第 73 次会话）", "工位 C 75 dB 最低，D 85 dB 最高。", "听力防护重点 D。", False, "车间噪音"),
    ("_2mm 板 90A 起弧（第 74 次会话）", "薄板 2mm 用 90A，烧穿就降 5A。", "薄板小电流。", True, "焊接"),
    ("_顶出 15%（第 75 次会话）", "顶出段占周期 15%，优化空间不大。", "冷却才是大头。", True, "注塑"),
    ("_B 线张紧每周（第 76 次会话）", "装配线输送带张紧每周查。", "张紧不足打滑。", True, "装配车间"),
]

RAW_QA = [
    ("A3", "齿轮箱振动新数据怎么说？", "第 17 次会话频谱显示 50Hz 尖峰，怀疑基础而不是齿隙。当时未下结论，留待复测。"),
    ("C1", "变频器 E17 是不是风扇堵转？", "Agent 当时回答：E17 是风扇堵转，换风扇即可。（后续复测证明该判断错误：E17=散热过温，风道堵塞为主因）"),
    ("C2", "变频器 E17 到底是什么？", "纠正后的回答：E17 = 散热过温。清洁风道与风扇，检查环境温度。"),
    ("D2", "高粉尘车间空压机保养多久一次？", "第 58 次会话回答：粉尘 >10 mg/m³ 时缩短至 300 小时，一般环境仍 500 小时。"),
    ("F1", "轴承温度到 80°C 是不是绝对安全？", "Agent 当时回答：80°C 绝对安全不用管。（错误：2024 版手册 75°C 即报警，该问答仅是存档，不要当经验）"),
    ("F2", "齿轮箱保养差不多就行吧？", "Agent 当时回答：差不多按周期来就行。（含糊问答，无具体数值）"),
    ("F3", "轴承温度 80°C 安全吗？", "与此前问答重复的提问，当时的回答同样是含糊的'应该没问题'。"),
    ("F4", "输送线链条伸长率怎么测？", "当时的提问没有得到有效回答（Agent 表示信息不足），仅存档。"),
    ("F5", "空压机维护周期是不是 1000 小时？", "按当时 2019 规程回答 1000 小时。（已过时：现行 500 小时，高粉尘 300 小时）"),
    ("N1", "空压机排气温度报警值是多少？", "105°C，报警并联锁停机。"),
    ("N2", "储气罐多久排污一次？", "每 500 小时。"),
    ("N3", "伺服位置环增益怎么设？", "速度环的 1/2 起步。"),
    ("N4", "光电传感器对准公差多少？", "5 mm。"),
    ("N5", "输送线链速多少？", "0.8 m/s。"),
    ("N6", "接地电阻要小于多少？", "4 Ω。"),
    ("N7", "振动 A 级限值是多少？", "0.71 mm/s（ISO 10816）。"),
    ("N8", "6205 轴承内径多大？", "25 mm。"),
    ("N9", "2.2kW 电机额定电流多少？", "约 4.7 A（380V 四极）。"),
    ("N10", "气缸 CQ2 有哪些行程？", "50/100/200 mm。"),
    ("N11", "滤芯 P-191116 库存多少？", "2 件，最低 1 件。"),
    ("N12", "新皮带挠度多少合适？", "跨距 1000mm 时 4.5 mm。"),
    ("N13", "M12 法兰螺栓拧多紧？", "65 Nm，对角分三次。"),
    ("N14", "2026 年大修几次？", "三次：4 月、8 月、12 月。"),
    ("N15", "装配车间点检频次？", "每周一次。"),
]


def _titles(database) -> set:
    return {
        str(entry.title)
        for entry in database.list_knowledge_memory_entries(limit=500)
    } | {
        str(entry.title)
        for entry in database.list_knowledge_memory_entries(limit=500, status="deleted")
    }


def _doc_id_by_title(database, title: str) -> int | None:
    for document in database.list_documents():
        if document.title == title:
            return document.id
    return None


def stage_memories() -> int:
    database = _database()
    service = _service(database)
    existing = _titles(database)
    {
        "空压机二号机完整手册": _doc_id_by_title(database, "空压机二号机完整手册"),
        "伺服驱动器调试手册": _doc_id_by_title(database, "伺服驱动器调试手册"),
        DELETED_SOURCE_TITLE: _doc_id_by_title(database, DELETED_SOURCE_TITLE),
    }

    created: dict[str, int] = {}
    for key, spec in EXPERIENCES:
        if spec["title"] in existing:
            print(f"skip existing memory: {spec['title']}")
            continue
        entry = service.create_entry(
            kind=spec["kind"],
            title=spec["title"],
            content=spec["content"],
            root_cause=spec.get("root_cause", ""),
            lesson=spec.get("lesson", ""),
            status="active",
            outcome=spec.get("outcome", ""),
            context_conditions=spec.get("context_conditions", ""),
            creation_origin="human_saved",
            root_cause_confirmed=spec.get("root_cause_confirmed", False),
        )
        created[key] = entry.id
        print(f"created memory id={entry.id}: {entry.title}")

    for title_text, experience, lesson, confirmed, context in ASSORTED_EXPERIENCES:
        if title_text in existing:
            print(f"skip existing memory: {title_text}")
            continue
        entry = service.create_entry(
            kind="experience",
            title=title_text,
            content=experience,
            lesson=lesson,
            status="active",
            context_conditions=context,
            creation_origin="human_saved",
            root_cause_confirmed=confirmed,
        )
        print(f"created memory id={entry.id}: {entry.title}")

    for key, question, answer in RAW_QA:
        if question in existing:
            print(f"skip existing raw_qa: {question}")
            continue
        result = service.create_raw_qa_entry(question=question, answer=answer)
        created[key] = result.entry.id
        print(f"created raw_qa id={result.entry.id}: {question}")

    # Longitudinal chain links (A1→A2→A4→A5, D1→D3) via memory links.
    links = [
        ("A2", "A1", "refines", "第 8 次会话修正第 1 次的齿隙怀疑"),
        ("A4", "A2", "supersedes", "第 29 次会话定位真正根因：地脚基础松动"),
        ("A5", "A4", "refines", "第 43 次会话复紧后达标验证"),
        ("D3", "D1", "supersedes", "第 58 次会话按环境分档更新旧周期结论"),
    ]
    for child, parent, relation, note in links:
        child_id = created.get(child)
        parent_id = created.get(parent)
        if not child_id or not parent_id:
            print(f"skip chain link {child}→{parent} (missing id)")
            continue
        service.link_experience_relation(
            from_entry_id=child_id,
            to_entry_id=parent_id,
            relation_type=relation,
            note=note,
        )
        print(f"linked {child}(id={child_id}) -{relation}-> {parent}(id={parent_id})")

    total = database.list_knowledge_memory_entries(limit=500)
    print(f"total live memories: {len(total)}")
    return 0


def stage_delete_source() -> int:
    from src.document_deletion_service import DocumentDeletionService

    settings = _settings()
    database = _database()
    service = DocumentDeletionService(
        database=database,
        raw_dir=settings.raw_dir,
        pages_dir=settings.pages_dir,
        markdown_dir=settings.markdown_dir,
        data_dir=settings.data_dir,
        agent_readings_dir=settings.agent_readings_dir,
    )
    for document in database.list_documents():
        if document.title == DELETED_SOURCE_TITLE:
            service.delete_document(document.id, expected_title=document.title)
            print(f"deleted source doc id={document.id} {document.title}")
            return 0
    print(f"skip: {DELETED_SOURCE_TITLE} 不存在（可能已删除）")
    return 0


def stage_manifest() -> int:
    database = _database()
    docs = [
        {"title": d.title, "sha256": d.sha256}
        for d in sorted(database.list_documents(), key=lambda item: item.id)
    ]
    memory_count = len(database.list_knowledge_memory_entries(limit=500))
    manifest = {
        "manifest_id": "dirty-long-term-v2",
        "frozen_at": datetime.now(UTC).isoformat(),
        "database_path_expected": str(_settings().database_path),
        "documents": docs,
        "knowledge_memory_entry_count": memory_count,
        "notes": (
            "v0.8.3 DIRTY_LONG_TERM V2：v1 基础上扩容（40 新文档 / 76 新记忆），"
            "含 4 条长期经验链、过期经验、错误问答、相似干扰对、删除来源文档"
            f"（{DELETED_SOURCE_TITLE}，manifest 冻结时已删除）。"
        ),
    }
    MANIFEST_V3.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"manifest written: {MANIFEST_V3.name} ({len(docs)} docs, {memory_count} memories)")
    return 0


if __name__ == "__main__":
    stage_memories()
