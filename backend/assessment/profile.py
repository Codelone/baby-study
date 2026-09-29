from assessment.questions import QUESTIONS, answer_label, branch_for

RULE_VERSION = "2026-09-v1"

BRANCH_LABELS = {
    "A": "亲子共读启蒙",
    "B": "互动式共读",
    "C": "早期识读探索",
}


def _selected(answers, question_id):
    value = answers.get(question_id)
    return set(value) if isinstance(value, list) else set()


def _evidence(answers, ids):
    return [
        {
            "question_id": qid,
            "question": QUESTIONS[qid]["title"],
            "answer": answer_label(qid, answers[qid]),
        }
        for qid in ids
        if qid in answers
    ]


def _certainty(ids):
    if len(ids) >= 2:
        return "有多处观察"
    if ids:
        return "初步线索"
    return "待观察"


def _section(section_id, title, summary, ids, answers, *, inferred=True):
    return {
        "id": section_id,
        "title": title,
        "summary": summary,
        "certainty": _certainty(ids) if inferred else ("家长报告" if ids else "待观察"),
        "evidence": _evidence(answers, ids),
    }


def _has_any(answers, qid, positive):
    value = answers.get(qid)
    if isinstance(value, list):
        return bool(set(value) & positive)
    return value in positive


def _positive_ids(answers, checks):
    return [qid for qid, values in checks.items() if _has_any(answers, qid, values)]


def _examples(answers, ids):
    ignored = {
        "unknown",
        "unobserved",
        "not_tried",
        "none",
        "leaves",
        "resists_questions",
        "rarely",
        "parent_reads",
        "too_hard",
    }
    examples = []
    for qid in ids:
        labels = {item["value"]: item["label"] for item in QUESTIONS[qid].get("options", [])}
        value = answers[qid]
        for item in value if isinstance(value, list) else [value]:
            if item not in ignored and item in labels:
                examples.append(labels[item])
            if len(examples) == 3:
                return "、".join(examples)
    return "、".join(examples)


def generate_profile(answers):
    branch = branch_for(answers)
    q07 = _selected(answers, "Q07")
    participation_ids = _positive_ids(
        answers,
        {
            "Q06": {"follow_child", "points_actions", "answers", "retells", "reads_text"},
            "Q07": {
                "stays",
                "turns_pages",
                "repeats_page",
                "points_picture",
                "word_to_picture",
                "imitates_action",
                "imitates_sound",
                "fills_words",
                "answers",
                "predicts",
                "narrates",
                "notices_print",
                "reads_text",
            },
            "A01": {"brief", "stays", "requests"},
            "A02": {"looks", "points", "actions", "sounds"},
            "A04": {"rhythm", "actions", "pictures", "interactive", "pen", "turns_pages"},
            "B02": {"gesture", "sound", "phrase", "sentence", "chinese", "mixed"},
            "B03": {"anticipates", "fills", "predicts", "connects", "retells"},
        },
    )
    response_ids = _positive_ids(
        answers,
        {
            "Q07": {"word_to_picture", "fills_words", "answers", "predicts", "narrates"},
            "A02": {"looks", "points", "actions", "sounds"},
            "B01": {"finds", "acts", "emotion", "sequence", "answers"},
            "B02": {"gesture", "sound", "phrase", "sentence", "chinese", "mixed"},
            "B03": {"anticipates", "fills", "predicts", "connects", "retells"},
            "C04": {"characters", "events", "sequence", "reasons", "ending"},
        },
    )
    print_ids = _positive_ids(
        answers,
        {
            "Q06": {"reads_text"},
            "Q07": {"notices_print", "reads_text"},
            "C01": {"print_picture", "asks_read", "letters", "signs", "words", "direction"},
            "C02": {"rhymes", "similar", "initial_sound", "segments", "blends"},
            "C03": {
                "recognizes_words",
                "sounds_out",
                "repeated_sentences",
                "supported_sentences",
                "independent",
            },
        },
    )

    routine_ids = [qid for qid in ("Q04", "Q05") if qid in answers]
    frequency = answer_label("Q04", answers["Q04"]) if "Q04" in answers else "尚未填写"
    comfortable = answer_label("Q05", answers["Q05"]) if "Q05" in answers else "尚未填写"
    routine_summary = f"最近两周通常每周 {frequency} 接触英文绘本；单次舒适时长：{comfortable}。"

    exposure_ids = [qid for qid in ("Q02", "Q03") if qid in answers]
    exposure_summary = (
        "家长报告的语言环境："
        + answer_label("Q02", answers["Q02"])
        + "；英语接触方式："
        + answer_label("Q03", answers["Q03"])
        + "。接触少不等于能力不足。"
    )

    participation_summary = (
        "已报告的参与方式：" + _examples(answers, participation_ids) + "。"
        if participation_ids
        else "当前还缺少孩子如何参与共读的具体观察。"
    )
    response_summary = (
        "已报告的回应表现："
        + _examples(answers, response_ids)
        + "。动作、中文和英语回应都可作为线索。"
        if response_ids
        else "理解和回应方式仍待在自然共读中观察。"
    )
    if answers.get("C03") == "independent":
        print_summary = "家长报告孩子尝试独立读简单内容，仍需通过实际共读验证。"
    elif print_ids:
        print_summary = (
            "文字或语音线索：" + _examples(answers, print_ids) + "。暂不据此推断独立阅读能力。"
        )
    else:
        print_summary = "目前缺少文字或语音方面的明确观察，不代表孩子尚未具备这些表现。"

    interests = [item for item in answers.get("Q08", []) if item not in {"none", "unknown"}]
    interest_labels = {item["value"]: item["label"] for item in QUESTIONS["Q08"]["options"]}
    interest_summary = (
        "待验证的兴趣线索：" + "、".join(interest_labels[item] for item in interests) + "。"
        if interests
        else "还没有明确的兴趣线索，先通过实际共读观察。"
    )
    goal_labels = {item["value"]: item["label"] for item in QUESTIONS["Q09"]["options"]}
    goal = answers.get("Q09", {})
    schedule = answers.get("Q10", {})
    goal_summary = "本月优先：" + goal_labels[goal["primary"]] + "；"
    if goal["secondary"]:
        goal_summary += "其次：" + "、".join(goal_labels[item] for item in goal["secondary"]) + "；"
    goal_summary += (
        "家庭每周可安排 "
        f"{schedule['available_days']} 天，单次最多 {schedule['max_session_minutes']} 分钟。"
        if schedule
        else "家庭时间安排尚未填写。"
    )
    graded = answers.get("Q11", {})
    level_ids = ["Q11"] if graded else []
    if graded.get("status") == "using":
        level_summary = (
            "家长报告："
            + answer_label("Q11", graded)
            + "。级别保留原生说法，不换算，也不推断独立阅读能力。"
        )
    elif graded.get("status") == "none":
        level_summary = "家长表示尚未开始使用分级读物。"
    else:
        level_summary = "尚无可用的分级读物信息。"

    sections = [
        _section(
            "exposure", "语言与英语接触", exposure_summary, exposure_ids, answers, inferred=False
        ),
        _section("routine", "共读习惯", routine_summary, routine_ids, answers, inferred=False),
        _section("participation", "参与方式", participation_summary, participation_ids, answers),
        _section("response", "理解与回应", response_summary, response_ids, answers),
        _section("print", "文字与语音线索", print_summary, print_ids, answers),
        _section("interests", "兴趣线索", interest_summary, ["Q08"] if interests else [], answers),
        _section("goals", "目标与家庭安排", goal_summary, ["Q09", "Q10"], answers, inferred=False),
        _section("levels", "分级读物经历", level_summary, level_ids, answers, inferred=False),
    ]

    conflicts = []
    if {"stays", "leaves"} <= q07:
        conflicts.append(
            {
                "text": "同时报告了愿意一起看和经常很快离开，请核对最近常见的情况。",
                "question_ids": ["Q07"],
            }
        )
    if answers.get("Q06") == "reads_text" and answers.get("C03") == "parent_reads":
        conflicts.append(
            {
                "text": "共读方式与当前阅读方式的描述不同，请核对是否针对不同的书。",
                "question_ids": ["Q06", "C03"],
            }
        )
    if (answers.get("Q06") == "retells" or "answers" in q07) and "rarely" in _selected(
        answers, "B02"
    ):
        conflicts.append(
            {
                "text": "回答或复述与很少回应同时出现，请核对发生情境。",
                "question_ids": [qid for qid in ("Q06", "Q07", "B02") if qid in answers],
            }
        )
    if answers.get("C03") == "independent" and answers.get("C05") in {
        "parent_reads",
        "every_page",
        "too_hard",
    }:
        conflicts.append(
            {
                "text": "独立阅读与当前级别需要较多帮助同时出现，请核对书本是否不同。",
                "question_ids": ["C03", "C05"],
            }
        )

    duration_by_band = {
        "under_three": 2,
        "three_to_five": 3,
        "five_to_ten": 5,
        "ten_to_fifteen": 10,
        "over_fifteen": 15,
    }
    start_minutes = duration_by_band.get(answers.get("Q05"), 3)
    if schedule:
        start_minutes = min(start_minutes, schedule["max_session_minutes"])
    uncertain_start = branch == "A" and answers.get("Q06") == "unknown" and q07 <= {"unknown"}
    if uncertain_start:
        start_text = "先从低要求的亲子朗读观察，不据此判断孩子处于某个能力阶段。"
    else:
        start_text = {
            "A": "从亲子朗读、跟随孩子翻页和动作参与开始。",
            "B": "从围绕图片的少量互动开始，接受动作、中文或英语回应。",
            "C": "从家长支持下的文字和声音探索开始，不要求孩子独立读完。",
        }[branch]
    no_questions = "resists_questions" in q07 or answers.get("B04") == "dislikes"
    if no_questions:
        start_text += " 先不主动提问，让孩子按自己的方式参与。"
    elif answers.get("B04") == "too_many":
        start_text += " 提问保持少量，孩子失去兴趣时停止。"
    if "leaves" in q07 or answers.get("A01") == "leaves":
        start_text += " 孩子很快离开时可提前结束，不需坚持读完。"
    if answers.get("A03") == "more":
        start_text += " 可重复读孩子更愿意参与的书。"
    elif answers.get("A03") in {"loses_interest", "dislikes"}:
        start_text += " 暂不强求重复读同一本。"
    if answers.get("A04") not in {None, "unknown"}:
        start_text += " 可优先尝试" + answer_label("A04", answers["A04"]) + "。"
    start_text += f" 可先参考每次约 {start_minutes} 分钟，愿意提前结束也可以。"
    starting_ids = [
        qid for qid in ("Q05", "Q06", "Q07", "A01", "A03", "A04", "B04", "Q10") if qid in answers
    ]

    gaps = []
    if answers.get("Q07") == ["unknown"] or not participation_ids:
        gaps.append("留意孩子最近怎样参与共读，例如翻页、指图或动作。")
    if answers.get("Q05") in {"variable", "unobserved"}:
        gaps.append("观察孩子比较舒适的单次共读时长。")
    if not response_ids:
        gaps.append("留意孩子能否借助图片、动作或语言回应。")
    if branch == "B" and answers.get("B04") in {"rarely_asked", "unknown"}:
        gaps.append("观察孩子对少量自然提问的接受程度。")
    if branch == "C" and answers.get("C02") == ["unobserved"]:
        gaps.append("留意押韵或词语声音是否引起孩子注意。")
    if not interests:
        gaps.append("通过实际共读留意孩子更愿意接触的内容或形式。")
    if graded.get("status") == "using" and any(
        not item["native_level"] for item in graded["systems"]
    ):
        gaps.append("所用分级读物的原生级别尚未全部确认。")
    if conflicts:
        gaps.append("核对画像中标出的回答差异。")

    return {
        "branch": branch,
        "branch_label": BRANCH_LABELS[branch],
        "sections": sections,
        "starting_point": {
            "summary": start_text,
            "suggested_minutes": start_minutes,
            "evidence": _evidence(answers, starting_ids),
        },
        "evidence_gaps": gaps,
        "conflicts": conflicts,
        "reported_levels": graded.get("systems", []) if graded.get("status") == "using" else [],
        "primary_level_system": graded.get("primary_system")
        if graded.get("status") == "using"
        else None,
    }
