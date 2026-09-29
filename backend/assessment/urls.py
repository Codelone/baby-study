from django.urls import path

from assessment import views

urlpatterns = [
    path("learner", views.learner, name="learner"),
    path("assessment/questions", views.questions, name="assessment-questions"),
    path("assessments", views.assessments, name="assessments"),
    path("assessments/draft", views.draft, name="assessment-draft"),
    path("assessments/<int:assessment_id>", views.assessment_detail, name="assessment-detail"),
    path(
        "assessments/<int:assessment_id>/responses/<str:question_id>",
        views.response_detail,
        name="assessment-response",
    ),
    path("assessments/<int:assessment_id>/complete", views.complete, name="assessment-complete"),
    path("state/current", views.current_state, name="state-current"),
    path("states/<int:state_id>", views.state_detail, name="state-detail"),
    path("states/<int:state_id>/confirm", views.confirm_state, name="state-confirm"),
]
