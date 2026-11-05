# -*- coding: utf-8 -*-
"""演示用样例数据：未配置密钥时让界面可预览。真实截图请配置密钥后运行。"""

DEMO_NOTE = "演示数据｜未配置 API 密钥，以下为内置样例，非真实检索结果"

_SAMPLES = {
    "教材依据": [
        {"title": "义务教育信息科技课程标准（2022年版）算法模块内容要求解读", "url": "http://www.moe.gov.cn/jyb_xxgk/",
         "date": "2022/04/21", "site": "中华人民共和国教育部",
         "passage": "第二学段要求学生“能用自然语言、流程图等方式描述简单算法”，第三学段进一步要求“能对算法执行过程进行验证与调试”。"},
        {"title": "《算法的初步认识》单元教材分析与教学建议（小学信息科技）", "url": "https://www.zjer.cn/",
         "date": "2024/08/20", "site": "浙江省教育厅教研室",
         "passage": "教材以“生活中的算法”为起点，通过排队、路线规划等情境，帮助学生体会“解决问题有步骤、步骤有次序”。"},
        {"title": "信息科技课程六条逻辑主线的学习进阶：从数据到算法", "url": "https://www.smartedu.cn/",
         "date": "2025/03/11", "site": "国家中小学智慧教育平台",
         "passage": "数据、算法、网络、信息处理、信息安全、人工智能六条主线并非并列，算法是信息处理的核心，需在真实问题中螺旋上升。"},
    ],
    "考点解析": [
        {"title": "小学信息科技“算法”单元易错点与作业设计建议", "url": "https://www.zxxk.com/",
         "date": "2025/09/02", "site": "学科网",
         "passage": "学生常把“流程图”等同于“程序”，也易混淆分支与循环的适用情境，宜用同一任务的两种流程图对比辨析。"},
        {"title": "小学生算法思维常见迷思概念梳理与教学对策", "url": "https://zhuanlan.zhihu.com/p/000000",
         "date": "2025/06/18", "site": "知乎",
         "passage": "典型迷思包括“步骤越多算法越好”“只要结果对过程无所谓”，可通过让学生给同伴的算法“找茬”来纠正。"},
        {"title": "近五年小学信息科技学业质量监测试题分析与命题趋势", "url": "https://www.cnki.net/",
         "date": "2024/12/05", "site": "中国知网",
         "passage": "情境任务类试题占比约六成，纯概念记忆题比例逐年下降，命题明显向问题解决与计算思维倾斜。"},
    ],
    "实践活动": [
        {"title": "不插电活动设计：用卡片排序体验算法的顺序结构", "url": "https://www.smartedu.cn/",
         "date": "2025/02/27", "site": "国家中小学智慧教育平台",
         "passage": "每组发放打乱步骤的卡片，要求排成“泡一杯茶”的完整流程，再交换检查，体会步骤缺失或颠倒会导致任务失败。"},
        {"title": "图形化编程实践：用顺序结构实现红绿灯模拟", "url": "https://www.zjer.cn/",
         "date": "2024/11/08", "site": "浙江省教育厅教研室",
         "passage": "在图形化编程环境中依次设置“亮红灯—等待—亮绿灯”三条指令，观察执行顺序改变后的效果差异。"},
        {"title": "猜数字游戏：分支与循环结构的综合应用课例", "url": "https://www.bilibili.com/video/",
         "date": "2025/10/08", "site": "哔哩哔哩",
         "passage": "以“猜数字”为载体，先画流程图再编程实现，学生在调试中自然理解“条件判断”与“重复执行”的必要性。"},
    ],
    "延伸拓展": [
        {"title": "推荐系统里的算法：为什么你刷到的视频越来越像", "url": "https://www.kepu.net.cn/",
         "date": "2025/07/30", "site": "中国科普博览",
         "passage": "推荐算法通过记录点击、停留等行为数据建立偏好模型，再对候选内容排序，其本质仍是一系列可描述的步骤。"},
        {"title": "中小学人工智能教育的最新政策与推进进展", "url": "http://www.xinhuanet.com/",
         "date": "2025/08/14", "site": "新华网",
         "passage": "多地已将人工智能基础内容纳入中小学课程，强调“体验—理解—应用”的梯度设计，避免过早陷入编程技巧训练。"},
        {"title": "计算思维培养的国际比较与本土化路径研究", "url": "https://www.cnki.net/",
         "date": "2024/11/22", "site": "中国知网",
         "passage": "研究比较了多国课标中计算思维的表述差异，指出“抽象—分解—建模—验证”是较具共识的能力链条。"},
    ],
}


def demo_pack(topic: str, tier: str = "lite") -> dict:
    import copy
    import yb_search as yb

    buckets = {}
    for cat, docs in _SAMPLES.items():
        items = []
        for d in copy.deepcopy(docs):
            item = dict(d)
            item["_cat"] = cat
            item["_dom"] = yb.domain_weight(item.get("url", ""))
            item["_rel"] = yb.relevance_score(item, yb.build_terms(topic))
            item["_fresh"] = yb.freshness_score(item.get("date", ""))
            item["_final"] = round(0.45 * item["_rel"] + 0.35 * item["_dom"] + 0.20 * item["_fresh"], 4)
            items.append(item)
        items.sort(key=lambda x: -x["_final"])
        buckets[cat] = items

    total = sum(len(v) for v in buckets.values())
    return {
        "topic": topic,
        "tier": tier,
        "tier_cn": yb.TIER_CN.get(tier, tier),
        "missing_fields": [f for f, o in yb.FIELD_OWNER.items() if tier not in o],
        "total_before": total,
        "total_after": total,
        "buckets": buckets,
        "elapsed": 0.0,
        "calls": 0,
        "sent_params": [],
        "errors": [],
        "demo": True,
        "demo_note": DEMO_NOTE,
    }
