import re
from datetime import date

from django.utils import timezone
from rest_framework.exceptions import ValidationError

QUESTIONNAIRE_VERSION = "2026-09-v1"


def options(*items):
    return [{"value": value, "label": label} for value, label in items]


QUESTIONS = {
    "Q01": {
        "title": "孩子现在多大？",
        "type": "birth_month",
        "hint": "填写出生年月，不需要具体日期。适用范围为 1 岁半至 7 岁。",
    },
    "Q02": {
        "title": "孩子平时主要接触哪些语言？",
        "type": "languages",
        "options": options(
            ("mandarin", "普通话"),
            ("dialect", "方言"),
            ("english", "英语"),
            ("other", "其他语言"),
            ("unknown", "不确定"),
        ),
        "frequency_options": options(
            ("almost_none", "几乎没有"),
            ("one_to_two", "每周 1～2 次"),
            ("three_to_five", "每周 3～5 次"),
            ("almost_daily", "几乎每天"),
            ("multiple_daily", "每天多次"),
            ("unknown", "不确定"),
        ),
        "exclusive": ["unknown"],
    },
    "Q03": {
        "title": "孩子现在通过哪些方式接触英语？",
        "type": "multi",
        "options": options(
            ("parent_reads", "家长读英文绘本"),
            ("songs", "童谣或儿歌"),
            ("reading_pen", "点读笔"),
            ("animation", "英文动画"),
            ("class", "英语课程"),
            ("self_browses", "自己翻看英文绘本"),
            ("speaks", "跟读或说英文"),
            ("graded", "阅读分级读物"),
            ("none", "暂时没有固定方式"),
            ("other", "其他"),
            ("unknown", "不确定"),
        ),
        "exclusive": ["none", "unknown"],
    },
    "Q04": {
        "title": "最近两周，通常一周有几天接触英文绘本？",
        "type": "single",
        "options": options(
            ("zero", "0 天"),
            ("one_to_two", "1～2 天"),
            ("three_to_four", "3～4 天"),
            ("five_to_six", "5～6 天"),
            ("daily", "每天"),
            ("unknown", "不确定"),
        ),
    },
    "Q05": {
        "title": "状态比较好时，孩子通常愿意一起看多久？",
        "type": "single",
        "options": options(
            ("under_three", "少于 3 分钟"),
            ("three_to_five", "3～5 分钟"),
            ("five_to_ten", "5～10 分钟"),
            ("ten_to_fifteen", "10～15 分钟"),
            ("over_fifteen", "15 分钟以上"),
            ("variable", "很不固定"),
            ("unobserved", "没观察过"),
        ),
    },
    "Q06": {
        "title": "看绘本时，通常是谁在主导？",
        "type": "single",
        "options": options(
            ("parent_reads", "家长从头读到尾，孩子听"),
            ("follow_child", "家长跟着孩子翻到哪里读到哪里"),
            ("points_actions", "家长读，孩子会指图、做动作或回应"),
            ("answers", "孩子会用动作或语言回答提问"),
            ("retells", "孩子会自己讲图或复述"),
            ("reads_text", "孩子会尝试自己读文字"),
            ("unknown", "没有固定方式或不确定"),
        ),
    },
    "Q07": {
        "title": "最近一起看书时，孩子通常有哪些表现？",
        "type": "multi",
        "hint": "只选择确实观察到的表现。",
        "options": options(
            ("stays", "愿意靠近并一起看"),
            ("turns_pages", "主动翻页或触摸机关"),
            ("repeats_page", "反复看某一页"),
            ("points_picture", "指向熟悉的图片"),
            ("word_to_picture", "听到词语后能指向相应图片"),
            ("imitates_action", "模仿动作"),
            ("imitates_sound", "模仿声音或单词"),
            ("fills_words", "补充熟悉的词或句子"),
            ("answers", "回答简单问题"),
            ("predicts", "预测接下来发生什么"),
            ("narrates", "根据图片讲内容"),
            ("notices_print", "关注字母或文字"),
            ("reads_text", "尝试读出词或句子"),
            ("leaves", "经常很快离开"),
            ("resists_questions", "容易因提问而抵触"),
            ("unknown", "没观察过或不确定"),
        ),
        "exclusive": ["unknown"],
    },
    "Q08": {
        "title": "孩子目前更容易被什么吸引？",
        "type": "multi",
        "options": options(
            ("animals", "动物"),
            ("vehicles", "交通工具"),
            ("actions", "身体动作"),
            ("daily_life", "家庭与日常生活"),
            ("food", "食物"),
            ("music", "音乐、节奏和童谣"),
            ("shapes", "数字、颜色和形状"),
            ("dinosaurs", "恐龙"),
            ("nature", "自然与科普"),
            ("humor", "幽默或重复情节"),
            ("interactive", "翻翻、触摸、立体等互动形式"),
            ("other", "其他"),
            ("none", "没有明显偏好"),
            ("unknown", "不确定"),
        ),
        "exclusive": ["none", "unknown"],
    },
    "Q09": {
        "title": "接下来一个月，你最希望先改善哪一件事？",
        "type": "goals",
        "options": options(
            ("habit", "建立稳定的英语共读习惯"),
            ("interest", "提高共读兴趣"),
            ("listening", "增加听力和语音输入"),
            ("expression", "鼓励指认、模仿或表达"),
            ("vocabulary", "扩展日常词汇"),
            ("story", "培养故事理解和表达"),
            ("print", "开始文字意识或自然拼读"),
            ("graded", "开始或推进分级阅读"),
            ("unknown", "不确定，希望系统建议"),
        ),
    },
    "Q10": {
        "title": "家庭比较容易坚持怎样的安排？",
        "type": "schedule",
        "windows": options(
            ("morning", "起床后"),
            ("daytime", "白天"),
            ("after_dinner", "晚饭后"),
            ("bedtime", "睡前"),
            ("variable", "不固定"),
        ),
        "duration_options": [3, 5, 10, 15, 20],
    },
    "Q11": {
        "title": "目前是否正在使用某套分级读物？",
        "type": "graded",
        "hint": "级别只记录家长提供的原生说法，不进行跨体系换算。",
        "options": options(
            ("oxford", "牛津树"),
            ("raz", "RAZ"),
            ("rocket", "红火箭"),
            ("random_house", "兰登"),
            ("pearson", "培生"),
            ("ladybird", "快乐瓢虫"),
            ("global_english", "环球英语分级"),
            ("other", "其他"),
        ),
        "usage_options": options(
            ("parent_reads", "主要由家长读"),
            ("together", "一起读"),
            ("child_reads", "孩子尝试自己读"),
            ("unknown", "不确定"),
        ),
    },
    "A01": {
        "title": "家长开始读一本孩子比较喜欢的书时，通常会发生什么？",
        "type": "single",
        "options": options(
            ("leaves", "很快离开"),
            ("brief", "会停留一小会儿"),
            ("stays", "大部分时间愿意一起看"),
            ("requests", "会主动拿书来或要求再读"),
            ("unknown", "不确定"),
        ),
    },
    "A02": {
        "title": "下面哪些行为最近出现过？",
        "type": "multi",
        "options": options(
            ("looks", "看向家长提到的图片"),
            ("points", "指向熟悉的物品或角色"),
            ("actions", "模仿书里的动作"),
            ("sounds", "模仿动物声、语气或节奏"),
            ("unobserved", "暂时没有观察到"),
            ("not_tried", "没试过"),
        ),
        "exclusive": ["unobserved", "not_tried"],
    },
    "A03": {
        "title": "孩子对重复读同一本书是什么反应？",
        "type": "single",
        "options": options(
            ("more", "更愿意参与"),
            ("same", "与第一次差不多"),
            ("loses_interest", "很快失去兴趣"),
            ("dislikes", "不喜欢重复"),
            ("unobserved", "没观察过"),
        ),
    },
    "A04": {
        "title": "哪种方式最容易让孩子参与？",
        "type": "single",
        "options": options(
            ("rhythm", "唱或念有节奏的内容"),
            ("actions", "做动作"),
            ("pictures", "指认图片"),
            ("interactive", "翻翻、触摸或立体机关"),
            ("pen", "点读"),
            ("turns_pages", "让孩子自己翻"),
            ("unknown", "暂不确定"),
        ),
    },
    "B01": {
        "title": "不要求孩子说英语时，哪些表现比较稳定？",
        "type": "multi",
        "options": options(
            ("finds", "能按提示找到人物或物品"),
            ("acts", "能完成简单动作指令"),
            ("emotion", "能指出明显的情绪或行为"),
            ("sequence", "能按先后顺序找出两幅图"),
            ("answers", "能回答简单的谁、什么、哪里问题"),
            ("unobserved", "没观察过"),
        ),
        "exclusive": ["unobserved"],
    },
    "B02": {
        "title": "孩子通常怎样回应绘本？",
        "type": "multi",
        "options": options(
            ("gesture", "用动作或指认"),
            ("sound", "发出声音或说单词"),
            ("phrase", "说短语"),
            ("sentence", "用句子描述图片"),
            ("chinese", "用中文讲述或解释"),
            ("mixed", "混合使用多种方式"),
            ("rarely", "很少回应"),
            ("unknown", "不确定"),
        ),
        "exclusive": ["rarely", "unknown"],
    },
    "B03": {
        "title": "读熟悉的故事时，哪些表现出现过？",
        "type": "multi",
        "options": options(
            ("anticipates", "提前做出动作或声音"),
            ("fills", "补充重复句中的词"),
            ("predicts", "预测下一页"),
            ("connects", "发现内容与生活的联系"),
            ("retells", "简单复述故事"),
            ("unobserved", "没观察过"),
        ),
        "exclusive": ["unobserved"],
    },
    "B04": {
        "title": "家长在阅读中提问时，孩子通常怎样？",
        "type": "single",
        "options": options(
            ("likes", "喜欢互动"),
            ("few_ok", "少量问题可以接受"),
            ("too_many", "问多了容易失去兴趣"),
            ("dislikes", "明显不喜欢被提问"),
            ("rarely_asked", "家长很少提问"),
            ("unknown", "不确定"),
        ),
    },
    "C01": {
        "title": "孩子目前会主动关注哪些内容？",
        "type": "multi",
        "options": options(
            ("print_picture", "知道文字与图片不同"),
            ("asks_read", "会指着文字让家长读"),
            ("letters", "认识部分字母"),
            ("signs", "认识自己的名字或熟悉标识"),
            ("words", "认识一些英文单词"),
            ("direction", "会跟随阅读方向"),
            ("unobserved", "没观察过"),
        ),
        "exclusive": ["unobserved"],
    },
    "C02": {
        "title": "哪些语音表现出现过？",
        "type": "multi",
        "options": options(
            ("rhymes", "喜欢押韵和节奏"),
            ("similar", "发现两个词听起来相似"),
            ("initial_sound", "尝试说出单词开头的声音"),
            ("segments", "尝试把简单单词拆成声音"),
            ("blends", "尝试把声音组合成词"),
            ("unobserved", "没观察过"),
        ),
        "exclusive": ["unobserved"],
    },
    "C03": {
        "title": "面对简单英文绘本或分级读物，孩子通常怎样读？",
        "type": "single",
        "options": options(
            ("parent_reads", "完全由家长读"),
            ("picture_telling", "根据图片说内容"),
            ("memorized", "背出熟悉的句子"),
            ("recognizes_words", "认出少量高频词"),
            ("sounds_out", "拼读部分简单词"),
            ("repeated_sentences", "读重复句型"),
            ("supported_sentences", "读简单句子，但需要帮助"),
            ("independent", "尝试独立读简单内容"),
            ("unknown", "没观察过或不确定"),
        ),
    },
    "C04": {
        "title": "读完简单内容后，哪些表现比较稳定？",
        "type": "multi",
        "options": options(
            ("characters", "指出主要人物或物品"),
            ("events", "说出发生了什么"),
            ("sequence", "排列简单情节顺序"),
            ("reasons", "说明简单原因"),
            ("ending", "预测或改编结局"),
            ("unobserved", "目前很少观察"),
        ),
        "exclusive": ["unobserved"],
    },
    "C05": {
        "title": "阅读当前分级级别时，通常需要多少帮助？",
        "type": "single",
        "options": options(
            ("parent_reads", "主要由家长读"),
            ("every_page", "每页都需要帮助"),
            ("occasional", "偶尔需要提示"),
            ("mostly_independent", "大部分可以自己完成"),
            ("too_easy", "内容明显太简单"),
            ("too_hard", "内容明显太难"),
            ("unknown", "不确定"),
        ),
    },
}

CORE_IDS = [f"Q{i:02d}" for i in range(1, 12)]
BRANCH_IDS = {
    "A": ["A01", "A02", "A03", "A04"],
    "B": ["B01", "B02", "B03", "B04"],
    "C": ["C01", "C02", "C03", "C04", "C05"],
}


def month_age(value, today=None):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}", value):
        return None
    try:
        born = date.fromisoformat(value + "-01")
    except ValueError:
        return None
    today = today or timezone.localdate()
    return (today.year - born.year) * 12 + today.month - born.month


def branch_for(answers):
    mode = answers.get("Q06")
    observed = set(answers.get("Q07") or [])
    if mode == "reads_text" or observed & {"notices_print", "reads_text"}:
        return "C"
    if mode in {"answers", "retells"} or observed & {
        "fills_words",
        "answers",
        "predicts",
        "narrates",
    }:
        return "B"
    return "A"


def visible_ids(answers):
    ids = CORE_IDS[:10]
    exposure = answers.get("Q03") or []
    age = month_age(answers.get("Q01"))
    if "graded" in exposure or (age is not None and age >= 36):
        ids.append("Q11")
    branch = branch_for(answers)
    ids += BRANCH_IDS[branch][:4]
    graded = answers.get("Q11") or {}
    if (
        branch == "C"
        and graded.get("status") == "using"
        and any(item.get("native_level", "").strip() for item in graded.get("systems", []))
    ):
        ids.append("C05")
    return ids


def public_questions(answers):
    return [{"id": qid, **QUESTIONS[qid]} for qid in visible_ids(answers)]


def _invalid(message):
    raise ValidationError({"answer_value": [message]})


def _validate_choices(value, question, *, single=False):
    allowed = {option["value"] for option in question["options"]}
    if single:
        if not isinstance(value, str) or value not in allowed:
            _invalid("请选择一个有效选项")
        return value
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or item not in allowed for item in value)
        or len(value) != len(set(value))
    ):
        _invalid("请选择有效且不重复的选项")
    if any(item in value for item in question.get("exclusive", [])) and len(value) != 1:
        _invalid("此选项不能与其他选项同时选择")
    return value


def validate_answer(question_id, value):
    question = QUESTIONS.get(question_id)
    if question is None:
        _invalid("未知题目")
    kind = question["type"]
    if kind == "single":
        return _validate_choices(value, question, single=True)
    if kind == "multi":
        return _validate_choices(value, question)
    if kind == "birth_month":
        age = month_age(value)
        if age is None or not 18 <= age <= 95:
            _invalid("出生年月需对应 18～95 月龄")
        return value
    if kind == "languages":
        if not isinstance(value, dict) or set(value) != {"languages", "english_frequency"}:
            _invalid("请填写语言和英语接触频率")
        languages = _validate_choices(value["languages"], question)
        frequencies = {item["value"] for item in question["frequency_options"]}
        if (
            not isinstance(value["english_frequency"], str)
            or value["english_frequency"] not in frequencies
        ):
            _invalid("请选择有效的英语接触频率")
        return {"languages": languages, "english_frequency": value["english_frequency"]}
    if kind == "goals":
        if not isinstance(value, dict) or set(value) != {"primary", "secondary"}:
            _invalid("请填写首要目标和可选的次要目标")
        allowed = {item["value"] for item in question["options"]}
        primary, secondary = value["primary"], value["secondary"]
        if (
            not isinstance(primary, str)
            or primary not in allowed
            or not isinstance(secondary, list)
            or len(secondary) > 2
            or any(not isinstance(item, str) for item in secondary)
        ):
            _invalid("目标选项无效")
        if any(item not in allowed or item in {primary, "unknown"} for item in secondary):
            _invalid("次要目标需与首要目标不同")
        if len(set(secondary)) != len(secondary) or (primary == "unknown" and secondary):
            _invalid("目标不能重复；不确定时不选次要目标")
        return {"primary": primary, "secondary": secondary}
    if kind == "schedule":
        if not isinstance(value, dict) or set(value) != {
            "available_days",
            "max_session_minutes",
            "preferred_windows",
            "weekend_differs",
        }:
            _invalid("请填写每周天数、单次时长和时间安排")
        days, minutes = value["available_days"], value["max_session_minutes"]
        windows = value["preferred_windows"]
        allowed = {item["value"] for item in question["windows"]}
        if type(days) is not int or not 1 <= days <= 7:
            _invalid("每周可安排天数需为 1～7")
        if type(minutes) is not int or not 3 <= minutes <= 60:
            _invalid("单次时长需为 3～60 分钟")
        if (
            not isinstance(windows, list)
            or any(not isinstance(item, str) for item in windows)
            or len(windows) != len(set(windows))
            or any(item not in allowed for item in windows)
        ):
            _invalid("常见时间段无效")
        if "variable" in windows and len(windows) > 1:
            _invalid("不固定不能与其他时段同时选择")
        if type(value["weekend_differs"]) is not bool:
            _invalid("请选择工作日和周末安排是否不同")
        return value
    if kind == "graded":
        if not isinstance(value, dict) or set(value) != {"status", "systems", "primary_system"}:
            _invalid("请填写分级读物使用情况")
        status, systems, primary = value["status"], value["systems"], value["primary_system"]
        if (
            not isinstance(status, str)
            or status not in {"none", "using", "unknown"}
            or not isinstance(systems, list)
        ):
            _invalid("分级读物状态无效")
        if status != "using":
            if systems or primary is not None:
                _invalid("尚未开始或不确定时不能填写体系")
            return value
        if not systems or len(systems) > 8:
            _invalid("请选择至少一个分级体系")
        allowed = {item["value"] for item in question["options"]}
        usages = {item["value"] for item in question["usage_options"]}
        seen = set()
        for item in systems:
            if not isinstance(item, dict) or set(item) != {"system", "native_level", "usage"}:
                _invalid("分级体系条目无效")
            system, level, usage = item["system"], item["native_level"], item["usage"]
            if (
                not isinstance(system, str)
                or not isinstance(usage, str)
                or system not in allowed
                or system in seen
                or usage not in usages
            ):
                _invalid("分级体系或主要使用方式无效")
            if not isinstance(level, str) or len(level.strip()) > 80:
                _invalid("原生级别不能超过 80 字")
            seen.add(system)
        if primary is not None and (not isinstance(primary, str) or primary not in seen):
            _invalid("主体系必须是已选择的体系")
        return {
            "status": status,
            "systems": [{**item, "native_level": item["native_level"].strip()} for item in systems],
            "primary_system": primary,
        }
    _invalid("无法识别的题型")


def answer_label(question_id, value):
    question = QUESTIONS[question_id]
    kind = question["type"]
    labels = {item["value"]: item["label"] for item in question.get("options", [])}
    if kind == "single":
        return labels[value]
    if kind == "multi":
        return "、".join(labels[item] for item in value)
    if kind == "birth_month":
        return value + " 出生"
    if kind == "languages":
        frequency = {item["value"]: item["label"] for item in question["frequency_options"]}
        return (
            "语言："
            + "、".join(labels[item] for item in value["languages"])
            + "; 英语接触："
            + frequency[value["english_frequency"]]
        )
    if kind == "goals":
        result = "首要：" + labels[value["primary"]]
        if value["secondary"]:
            result += "; 次要：" + "、".join(labels[item] for item in value["secondary"])
        return result
    if kind == "schedule":
        windows = {item["value"]: item["label"] for item in question["windows"]}
        chosen = "、".join(windows[item] for item in value["preferred_windows"]) or "未指定"
        result = (
            f"每周 {value['available_days']} 天，"
            f"单次最多 {value['max_session_minutes']} 分钟；常见时段：{chosen}"
        )
        if value["weekend_differs"]:
            result += "；工作日与周末安排不同"
        return result
    if kind == "graded":
        if value["status"] != "using":
            return "尚未开始" if value["status"] == "none" else "不确定"
        usages = {item["value"]: item["label"] for item in question["usage_options"]}
        result = "、".join(
            labels[item["system"]]
            + (" " + item["native_level"] if item["native_level"] else "（级别未填）")
            + "（"
            + usages[item["usage"]]
            + "）"
            for item in value["systems"]
        )
        if value["primary_system"]:
            result += "；希望延续：" + labels[value["primary_system"]]
        return result
    return ""
