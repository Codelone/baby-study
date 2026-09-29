from django.conf import settings
from django.db import models
from django.db.models import Q


class Learner(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    birth_year_month = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user} 的孩子"


class AssessmentSession(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "填写中"
        COMPLETED = "completed", "已完成"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    learner = models.ForeignKey(Learner, on_delete=models.CASCADE)
    questionnaire_version = models.CharField(max_length=32)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    copied_from = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-started_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("user",), condition=Q(status="draft"), name="one_draft_assessment_per_user"
            )
        ]


class AssessmentResponse(models.Model):
    session = models.ForeignKey(
        AssessmentSession, related_name="responses", on_delete=models.CASCADE
    )
    question_id = models.CharField(max_length=8)
    answer_value = models.JSONField()
    answered_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("session", "question_id"), name="one_answer_per_question_session"
            )
        ]


class LearnerStateVersion(models.Model):
    learner = models.ForeignKey(Learner, related_name="state_versions", on_delete=models.CASCADE)
    session = models.OneToOneField(
        AssessmentSession, related_name="state_version", on_delete=models.CASCADE
    )
    version = models.PositiveIntegerField()
    rule_version = models.CharField(max_length=32)
    profile = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-version",)
        constraints = [
            models.UniqueConstraint(
                fields=("learner", "version"), name="unique_learner_state_version"
            )
        ]
