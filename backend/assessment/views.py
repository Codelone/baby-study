from datetime import date

from django.db import transaction
from django.db.models import Max
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from assessment.models import AssessmentResponse, AssessmentSession, Learner, LearnerStateVersion
from assessment.profile import RULE_VERSION, generate_profile
from assessment.questions import (
    QUESTIONNAIRE_VERSION,
    QUESTIONS,
    public_questions,
    validate_answer,
    visible_ids,
)


class Conflict(APIException):
    status_code = 409
    default_detail = "当前状态不允许此操作"
    default_code = "conflict"


def _answers(session):
    return {item.question_id: item.answer_value for item in session.responses.all()}


def _session_data(session):
    answers = _answers(session)
    return {
        "id": session.id,
        "status": session.status,
        "questionnaire_version": session.questionnaire_version,
        "copied_from_id": session.copied_from_id,
        "started_at": session.started_at,
        "completed_at": session.completed_at,
        "answers": answers,
        "visible_question_ids": visible_ids(answers),
        "state_id": getattr(getattr(session, "state_version", None), "id", None),
    }


def _state_data(state):
    return {
        "id": state.id,
        "assessment_id": state.session_id,
        "version": state.version,
        "questionnaire_version": state.session.questionnaire_version,
        "rule_version": state.rule_version,
        "created_at": state.created_at,
        "confirmed_at": state.confirmed_at,
        "profile": state.profile,
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def learner(request):
    child = Learner.objects.filter(user=request.user).first()
    return Response(
        {
            "learner": {
                "id": child.id,
                "birth_year_month": child.birth_year_month.strftime("%Y-%m")
                if child.birth_year_month
                else None,
            }
            if child
            else None
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def questions(request):
    raw_session_id = request.query_params.get("session_id")
    answers = {}
    if raw_session_id:
        if not raw_session_id.isdecimal():
            raise ValidationError({"session_id": ["无效的评估编号"]})
        session = get_object_or_404(AssessmentSession, pk=int(raw_session_id), user=request.user)
        answers = _answers(session)
    return Response(
        {
            "questionnaire_version": QUESTIONNAIRE_VERSION,
            "questions": public_questions(answers),
        }
    )


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def assessments(request):
    if request.method == "GET":
        sessions = AssessmentSession.objects.filter(user=request.user).prefetch_related("responses")
        return Response({"assessments": [_session_data(session) for session in sessions]})

    version = request.data.get("questionnaire_version")
    if version != QUESTIONNAIRE_VERSION:
        raise ValidationError({"questionnaire_version": ["问卷版本已更新，请刷新页面"]})
    copied_from_id = request.data.get("copy_from_id")
    if copied_from_id is not None and (type(copied_from_id) is not int or copied_from_id <= 0):
        raise ValidationError({"copy_from_id": ["无效的来源评估编号"]})
    with transaction.atomic():
        child, _created = Learner.objects.get_or_create(user=request.user)
        child = Learner.objects.select_for_update().get(pk=child.pk)
        source = None
        if copied_from_id is not None:
            source = get_object_or_404(
                AssessmentSession,
                pk=copied_from_id,
                user=request.user,
                status=AssessmentSession.Status.COMPLETED,
            )
        draft = AssessmentSession.objects.filter(
            user=request.user, status=AssessmentSession.Status.DRAFT
        ).first()
        if draft:
            if source and draft.copied_from_id != source.id:
                raise Conflict("已有其他未完成的评估，请先继续或删除该草稿")
            return Response({"assessment": _session_data(draft)}, status=200)
        draft = AssessmentSession.objects.create(
            user=request.user,
            learner=child,
            questionnaire_version=QUESTIONNAIRE_VERSION,
            copied_from=source,
        )
        if source:
            AssessmentResponse.objects.bulk_create(
                [
                    AssessmentResponse(
                        session=draft,
                        question_id=item.question_id,
                        answer_value=item.answer_value,
                    )
                    for item in source.responses.all()
                ]
            )
    return Response({"assessment": _session_data(draft)}, status=201)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def draft(request):
    session = (
        AssessmentSession.objects.filter(user=request.user, status=AssessmentSession.Status.DRAFT)
        .prefetch_related("responses")
        .first()
    )
    return Response({"assessment": _session_data(session) if session else None})


@api_view(["GET", "DELETE"])
@permission_classes([IsAuthenticated])
def assessment_detail(request, assessment_id):
    session = get_object_or_404(
        AssessmentSession.objects.prefetch_related("responses"),
        pk=assessment_id,
        user=request.user,
    )
    if request.method == "DELETE":
        if session.status != AssessmentSession.Status.DRAFT:
            raise Conflict("已完成的评估不能删除")
        session.delete()
        return Response(status=204)
    return Response({"assessment": _session_data(session)})


@api_view(["PUT"])
@permission_classes([IsAuthenticated])
def response_detail(request, assessment_id, question_id):
    with transaction.atomic():
        session = get_object_or_404(
            AssessmentSession.objects.select_for_update(), pk=assessment_id, user=request.user
        )
        if session.status != AssessmentSession.Status.DRAFT:
            raise Conflict("已完成的评估不能修改，请从原答案创建新评估")
        if question_id not in QUESTIONS:
            raise ValidationError({"question_id": ["未知题目"]})
        answers = _answers(session)
        if question_id not in visible_ids(answers):
            raise ValidationError({"question_id": ["当前条件下不显示这道题"]})
        if not isinstance(request.data, dict) or set(request.data) != {"answer_value"}:
            raise ValidationError({"answer_value": ["请提交 answer_value"]})
        normalized = validate_answer(question_id, request.data["answer_value"])
        AssessmentResponse.objects.update_or_create(
            session=session, question_id=question_id, defaults={"answer_value": normalized}
        )
    return Response({"assessment": _session_data(session)})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def complete(request, assessment_id):
    with transaction.atomic():
        session = get_object_or_404(
            AssessmentSession.objects.select_for_update().select_related("learner"),
            pk=assessment_id,
            user=request.user,
        )
        if session.status == AssessmentSession.Status.COMPLETED:
            return Response({"state": _state_data(session.state_version)})
        answers = _answers(session)
        shown = visible_ids(answers)
        missing = [qid for qid in shown if qid not in answers]
        if missing:
            raise ValidationError({"answers": [f"请先完成：{', '.join(missing)}"]})
        active_answers = {qid: answers[qid] for qid in shown}
        child = Learner.objects.select_for_update().get(pk=session.learner_id)
        max_version = (
            LearnerStateVersion.objects.filter(learner=child).aggregate(Max("version"))[
                "version__max"
            ]
            or 0
        )
        profile = generate_profile(active_answers)
        state = LearnerStateVersion.objects.create(
            learner=child,
            session=session,
            version=max_version + 1,
            rule_version=RULE_VERSION,
            profile=profile,
        )
        birth = date.fromisoformat(active_answers["Q01"] + "-01")
        if child.birth_year_month != birth:
            child.birth_year_month = birth
            child.save(update_fields=("birth_year_month", "updated_at"))
        session.status = AssessmentSession.Status.COMPLETED
        session.completed_at = timezone.now()
        session.save(update_fields=("status", "completed_at"))
    return Response({"state": _state_data(state)}, status=201)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def current_state(request):
    state = (
        LearnerStateVersion.objects.select_related("session")
        .filter(learner__user=request.user)
        .first()
    )
    return Response({"state": _state_data(state) if state else None})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def state_detail(request, state_id):
    state = get_object_or_404(
        LearnerStateVersion.objects.select_related("session"),
        pk=state_id,
        learner__user=request.user,
    )
    return Response({"state": _state_data(state)})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def confirm_state(request, state_id):
    with transaction.atomic():
        state = get_object_or_404(
            LearnerStateVersion.objects.select_for_update().select_related("session"),
            pk=state_id,
            learner__user=request.user,
        )
        latest_id = (
            LearnerStateVersion.objects.filter(learner=state.learner)
            .order_by("-version")
            .values_list("id", flat=True)
            .first()
        )
        if state.id != latest_id:
            raise Conflict("只能确认当前画像")
        if state.confirmed_at is None:
            state.confirmed_at = timezone.now()
            state.save(update_fields=("confirmed_at",))
    return Response({"state": _state_data(state)})
