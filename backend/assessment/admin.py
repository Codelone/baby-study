from django.contrib import admin

from assessment.models import AssessmentResponse, AssessmentSession, Learner, LearnerStateVersion


@admin.register(Learner)
class LearnerAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "birth_year_month")
    readonly_fields = ("user", "birth_year_month", "created_at", "updated_at")


@admin.register(AssessmentSession)
class AssessmentSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "status", "questionnaire_version", "completed_at")
    readonly_fields = [field.name for field in AssessmentSession._meta.fields]


@admin.register(AssessmentResponse)
class AssessmentResponseAdmin(admin.ModelAdmin):
    list_display = ("id", "session", "question_id", "answered_at")
    readonly_fields = [field.name for field in AssessmentResponse._meta.fields]


@admin.register(LearnerStateVersion)
class LearnerStateVersionAdmin(admin.ModelAdmin):
    list_display = ("id", "learner", "version", "rule_version", "confirmed_at")
    readonly_fields = [field.name for field in LearnerStateVersion._meta.fields]
