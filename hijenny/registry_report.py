"""Plain-text tables for archived ClinicalTrials.gov observations."""


def cell(value):
    return "未提供" if value is None or value == "" else str(value)


def table(headers, rows):
    return [" | ".join(headers), *(" | ".join(map(cell, row)) for row in rows)] if rows else ["登记未提供此表的数据行。"]


def group_name(group):
    return " · ".join(str(group[key]) for key in ("id", "title") if group.get(key))


def safety_text(summary):
    lines = [f"报告期 · {cell(summary.get('timeFrame'))}",
             f"其他不良事件报告阈值（%） · {cell(summary.get('frequencyThreshold'))}"]
    if summary.get("description"):
        lines.append(summary["description"])
    rows = []
    for group in summary.get("eventGroups", []):
        name = group_name(group)
        if group.get("description"):
            lines.append(f"{name} · {group['description']}")
        for key, label in (("deaths", "全因死亡"), ("serious", "严重不良事件"), ("other", "其他不良事件")):
            affected, at_risk = group.get(key + "NumAffected"), group.get(key + "NumAtRisk")
            if affected is not None or at_risk is not None:
                rows.append([label, name, affected, at_risk])
    return "\n".join(lines + table(["类别", "报告组", "受影响人数", "风险人数"], rows))


ANALYSIS_FIELDS = {
    "groupIds": "纳入分析的组", "paramType": "效应量类型", "paramValue": "效应估计值", "ciPctValue": "置信水平（%）",
    "ciNumSides": "区间侧数", "ciLowerLimit": "区间下限", "ciUpperLimit": "区间上限", "pValue": "P 值",
    "statisticalMethod": "检验方法", "statisticalComment": "方法说明", "nonInferiorityType": "比较类型",
    "groupDescription": "组别说明", "estimateComment": "估计值说明", "pValueComment": "P 值说明",
}


def outcome_text(measure):
    lines = [f"主要终点 · {cell(measure.get('title'))}"]
    for field, label in (("populationDescription", "分析人群"), ("timeFrame", "评价时间"), ("reportingStatus", "报告状态"),
                         ("paramType", "统计量"), ("dispersionType", "离散量或区间类型")):
        lines.append(f"{label} · {cell(measure.get(field))}")
    groups = {g["id"]: group_name(g) for g in measure.get("groups", [])}
    denoms, values = [], []

    def collect_denoms(scope, block):
        for denom in block.get("denoms", []):
            for count in denom.get("counts", []):
                gid = count.get("groupId")
                denoms.append([scope, groups.get(gid, gid), count.get("value"), denom.get("units")])

    collect_denoms("整个终点", measure)
    for section in measure.get("classes", []):
        title = section.get("title")
        collect_denoms(title or "分层", section)
        for category in section.get("categories", []):
            scope = " / ".join(filter(None, (title, category.get("title")))) or "总体"
            collect_denoms(scope, category)
            for obs in category.get("measurements", []):
                gid = obs.get("groupId")
                values.append([scope, groups.get(gid, gid), obs.get("value"), measure.get("unitOfMeasure"),
                               obs.get("spread"), obs.get("lowerLimit"), obs.get("upperLimit"), obs.get("comment")])
    lines += ["分析分母", *table(["适用范围", "报告组", "人数或数量", "分母单位"], denoms),
              "分组测量", *table(["分层或类别", "报告组", "登记值", "单位", "离散量", "下限", "上限", "原注"], values)]
    rows = []
    for index, analysis in enumerate(measure.get("analyses", []), 1):
        for key, value in analysis.items():
            if key == "groupIds":
                value = " / ".join(groups.get(gid, gid) for gid in value)
            rows.append([index, ANALYSIS_FIELDS.get(key, key), value])
    lines += ["登记统计分析"]
    lines += table(["分析序号", "字段", "原报内容"], rows) if "analyses" in measure else ["此版归档尚未采集统计分析。"]
    return "\n".join(lines)
