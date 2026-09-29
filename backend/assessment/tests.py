import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from assessment.models import AssessmentResponse, AssessmentSession, LearnerStateVersion
from assessment.questions import QUESTIONNAIRE_VERSION, branch_for, visible_ids


def birth_month(years_ago=2):
    today = timezone.localdate()
    return f"{today.year - years_ago:04d}-{today.month:02d}"


def standard_answers():
    return {
        "Q01": birth_month(),
        "Q02": {"languages": ["mandarin"], "english_frequency": "one_to_two"},
        "Q03": ["parent_reads"],
        "Q04": "one_to_two",
        "Q05": "five_to_ten",
        "Q06": "parent_reads",
        "Q07": ["stays", "turns_pages"],
        "Q08": ["animals"],
        "Q09": {"primary": "habit", "secondary": ["interest"]},
        "Q10": {
            "available_days": 3,
            "max_session_minutes": 10,
            "preferred_windows": ["bedtime"],
            "weekend_differs": False,
        },
        "A01": "stays",
        "A02": ["points"],
        "A03": "more",
        "A04": "pictures",
    }


class AssessmentTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="parent-assessment", password="test-password"
        )
        self.client.force_login(self.user)

    def create_draft(self, copy_from_id=None):
        payload = {"questionnaire_version": QUESTIONNAIRE_VERSION}
        if copy_from_id is not None:
            payload["copy_from_id"] = copy_from_id
        return self.client.post(
            reverse("assessments"), json.dumps(payload), content_type="application/json"
        )

    def put_answer(self, session_id, question_id, value):
        return self.client.put(
            reverse("assessment-response", args=[session_id, question_id]),
            json.dumps({"answer_value": value}),
            content_type="application/json",
        )

    def fill(self, session_id, answers):
        for qid, value in answers.items():
            response = self.put_answer(session_id, qid, value)
            self.assertEqual(response.status_code, 200, (qid, response.content))

    def finish(self, session_id):
        return self.client.post(
            reverse("assessment-complete", args=[session_id]),
            "{}",
            content_type="application/json",
        )

    def test_draft_resumes_and_completion_preserves_immutable_answers(self):
        created = self.create_draft()
        self.assertEqual(created.status_code, 201)
        session_id = created.json()["assessment"]["id"]
        self.assertEqual(self.create_draft().json()["assessment"]["id"], session_id)
        self.put_answer(session_id, "Q01", birth_month())
        self.assertEqual(
            self.client.get(reverse("assessment-draft")).json()["assessment"]["answers"]["Q01"],
            birth_month(),
        )
        self.fill(session_id, {k: v for k, v in standard_answers().items() if k != "Q01"})
        completed = self.finish(session_id)
        self.assertEqual(completed.status_code, 201, completed.content)
        self.assertEqual(completed.json()["state"]["version"], 1)
        self.assertEqual(
            self.finish(session_id).json()["state"]["id"], completed.json()["state"]["id"]
        )
        self.assertEqual(LearnerStateVersion.objects.count(), 1)
        rejected = self.put_answer(session_id, "Q07", ["unknown"])
        self.assertEqual(rejected.status_code, 409)
        self.assertEqual(
            AssessmentResponse.objects.get(session_id=session_id, question_id="Q07").answer_value,
            ["stays", "turns_pages"],
        )
        state = completed.json()["state"]["profile"]
        participation = next(item for item in state["sections"] if item["id"] == "participation")
        self.assertEqual(participation["certainty"], "有多处观察")
        self.assertIn("主动翻页", participation["summary"])
        self.assertTrue(any(item["question_id"] == "Q07" for item in participation["evidence"]))
        exposure = next(item for item in state["sections"] if item["id"] == "exposure")
        self.assertIn("接触少不等于能力不足", exposure["summary"])
        self.assertEqual(state["starting_point"]["suggested_minutes"], 5)

    def test_branch_selection_and_conditional_questions(self):
        answers = standard_answers()
        self.assertEqual(branch_for(answers), "A")
        self.assertNotIn("Q11", visible_ids(answers))
        answers["Q01"] = birth_month(4)
        self.assertIn("Q11", visible_ids(answers))
        answers["Q01"] = birth_month()
        answers["Q03"] = ["graded"]
        self.assertIn("Q11", visible_ids(answers))
        self.assertEqual(branch_for(answers), "A")
        answers["Q06"] = "answers"
        self.assertEqual(branch_for(answers), "B")
        self.assertIn("B04", visible_ids(answers))
        answers["Q07"] = ["notices_print"]
        self.assertEqual(branch_for(answers), "C")
        self.assertNotIn("C05", visible_ids(answers))
        answers["Q11"] = {
            "status": "using",
            "systems": [{"system": "raz", "native_level": "AA", "usage": "parent_reads"}],
            "primary_system": "raz",
        }
        self.assertIn("C05", visible_ids(answers))

    def test_unknown_and_resistance_do_not_become_ability_deficits(self):
        created = self.create_draft()
        session_id = created.json()["assessment"]["id"]
        answers = standard_answers()
        answers.update(
            {
                "Q04": "zero",
                "Q05": "unobserved",
                "Q06": "unknown",
                "Q07": ["unknown"],
                "Q08": ["unknown"],
                "A01": "unknown",
                "A02": ["not_tried"],
                "A03": "unobserved",
                "A04": "unknown",
            }
        )
        self.fill(session_id, answers)
        profile = self.finish(session_id).json()["state"]["profile"]
        self.assertEqual(profile["branch"], "A")
        self.assertIn("不据此判断", profile["starting_point"]["summary"])
        self.assertEqual(profile["starting_point"]["suggested_minutes"], 3)
        response = next(item for item in profile["sections"] if item["id"] == "response")
        self.assertEqual(response["certainty"], "待观察")
        self.assertIn("仍待", response["summary"])
        self.assertFalse(profile["reported_levels"])

    def test_c_branch_level_claim_and_hidden_answers(self):
        created = self.create_draft()
        session_id = created.json()["assessment"]["id"]
        answers = standard_answers()
        answers.update(
            {
                "Q03": ["graded"],
                "Q06": "reads_text",
                "Q07": ["notices_print"],
                "Q11": {
                    "status": "using",
                    "systems": [{"system": "raz", "native_level": "AA", "usage": "parent_reads"}],
                    "primary_system": "raz",
                },
                "C01": ["letters"],
                "C02": ["unobserved"],
                "C03": "parent_reads",
                "C04": ["unobserved"],
                "C05": "every_page",
            }
        )
        for qid in ("A01", "A02", "A03", "A04"):
            answers.pop(qid)
        self.fill(session_id, answers)
        profile = self.finish(session_id).json()["state"]["profile"]
        self.assertEqual(profile["branch"], "C")
        self.assertEqual(profile["reported_levels"][0]["native_level"], "AA")
        self.assertTrue(profile["conflicts"])
        level = next(item for item in profile["sections"] if item["id"] == "levels")
        self.assertIn("RAZ AA", level["summary"])
        self.assertIn("主要由家长读", level["evidence"][0]["answer"])
        print_state = next(item for item in profile["sections"] if item["id"] == "print")
        self.assertIn("暂不据此推断独立阅读能力", print_state["summary"])

    def test_correction_creates_new_version_and_history(self):
        first = self.create_draft().json()["assessment"]["id"]
        self.fill(first, standard_answers())
        old_state = self.finish(first).json()["state"]
        copied = self.create_draft(copy_from_id=first).json()["assessment"]
        self.assertEqual(copied["answers"]["Q07"], ["stays", "turns_pages"])
        second = copied["id"]
        self.put_answer(second, "Q07", ["answers"])
        for qid, value in {
            "B01": ["finds"],
            "B02": ["chinese"],
            "B03": ["predicts"],
            "B04": "few_ok",
        }.items():
            self.assertEqual(self.put_answer(second, qid, value).status_code, 200)
        new_state = self.finish(second).json()["state"]
        self.assertEqual(new_state["version"], 2)
        self.assertEqual(new_state["profile"]["branch"], "B")
        response = next(
            item for item in new_state["profile"]["sections"] if item["id"] == "response"
        )
        self.assertIn("用中文讲述", response["summary"])
        self.assertEqual(
            self.client.get(reverse("state-current")).json()["state"]["id"], new_state["id"]
        )
        self.assertEqual(
            self.client.get(reverse("state-detail", args=[old_state["id"]])).json()["state"][
                "profile"
            ]["branch"],
            "A",
        )
        self.assertEqual(
            self.client.post(reverse("state-confirm", args=[old_state["id"]])).status_code, 409
        )
        self.assertEqual(
            self.client.post(reverse("state-confirm", args=[new_state["id"]])).status_code, 200
        )

    def test_validation_auth_and_csrf(self):
        session_id = self.create_draft().json()["assessment"]["id"]
        invalid = self.put_answer(session_id, "Q07", ["stays", "unknown"])
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("details", invalid.json()["error"])
        self.assertEqual(self.put_answer(session_id, "C01", ["letters"]).status_code, 400)
        self.assertEqual(self.finish(session_id).status_code, 400)
        self.assertEqual(LearnerStateVersion.objects.count(), 0)
        another = get_user_model().objects.create_user(username="other-parent", password="test")
        self.client.force_login(another)
        self.assertEqual(
            self.client.get(reverse("assessment-detail", args=[session_id])).status_code, 404
        )
        self.assertEqual(self.put_answer(session_id, "Q04", "daily").status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("assessment-draft")).status_code, 403)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(
            csrf_client.post(
                reverse("assessments"),
                json.dumps({"questionnaire_version": QUESTIONNAIRE_VERSION}),
                content_type="application/json",
            ).status_code,
            403,
        )

    def test_invalid_shapes_return_validation_errors(self):
        session_id = self.create_draft().json()["assessment"]["id"]
        invalid_cases = {
            "Q01": "2020-14",
            "Q02": {"languages": ["mandarin"], "english_frequency": []},
            "Q09": {"primary": [], "secondary": []},
            "Q10": {
                "available_days": 2,
                "max_session_minutes": 5,
                "preferred_windows": [{}],
                "weekend_differs": False,
            },
        }
        for qid, value in invalid_cases.items():
            self.assertEqual(self.put_answer(session_id, qid, value).status_code, 400, qid)

    def test_user_deletion_removes_assessment_history(self):
        session_id = self.create_draft().json()["assessment"]["id"]
        self.fill(session_id, standard_answers())
        self.finish(session_id)
        self.user.delete()
        self.assertFalse(AssessmentSession.objects.exists())
        self.assertFalse(LearnerStateVersion.objects.exists())
