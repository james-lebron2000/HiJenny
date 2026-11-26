"""Interpret directional PubMed publication-integrity links."""
import re


def publication_notices(citation, publication_types):
    relations = {
        "RetractionIn": "本文已有撤稿通知", "RetractionOf": "本记录是撤稿通知，所涉原文",
        "ErratumIn": "本文已有更正通知", "ErratumFor": "本记录是更正通知，所涉原文",
        "ExpressionOfConcernIn": "本文已有编辑关注声明", "ExpressionOfConcernFor": "本记录是编辑关注声明，所涉原文",
        "CorrectedandRepublishedIn": "本文已有更正重刊版", "CorrectedandRepublishedFrom": "本记录是更正重刊版，所涉原文",
        "RetractedandRepublishedIn": "本文已撤回并重刊", "RetractedandRepublishedFrom": "本记录是撤回后的重刊版，所涉原文",
    }
    notices = []
    for row in citation.findall("CommentsCorrectionsList/CommentsCorrections"):
        label = relations.get(row.get("RefType"))
        if not label:
            continue
        pmid = row.findtext("PMID", "").strip()
        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if re.fullmatch(r"[0-9]+", pmid) else ""
        notices.append(" · ".join(filter(None, (label, row.findtext("RefSource"), url))))
    types = {"Retracted Publication": "已撤稿文献", "Retraction of Publication": "撤稿通知",
             "Published Erratum": "更正通知", "Expression of Concern": "编辑关注声明"}
    return list(dict.fromkeys([f"PubMed 文献类型 · {types[t]}" for t in publication_types if t in types] + notices))
