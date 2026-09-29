from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from catalog.intake.costs import estimate_cost
from catalog.models import BookIntake, IntakePhoto


def returned_photo(inputs=2174, outputs=308, model="mimo-v2.6-flash"):
    return {
        "extraction": {
            "model_name": model,
            "usage": {"prompt_tokens": inputs, "completion_tokens": outputs},
        }
    }


@override_settings(MIMO_COST_PRICING_REGION="cn")
class IntakeCostTests(SimpleTestCase):
    def test_realtime_and_batch_use_distinct_rates_without_float_rounding(self):
        photos = [returned_photo()]
        self.assertEqual(estimate_cost(photos, "realtime")["amount"], "0.002790000")
        self.assertEqual(estimate_cost(photos, "batch")["amount"], "0.001395000")

    def test_missing_and_unknown_usage_are_excluded_from_coverage(self):
        photos = [
            returned_photo(),
            {},
            returned_photo(model="unpriced-model"),
            returned_photo(None),
        ]
        result = estimate_cost(photos, "batch")
        self.assertEqual(result["amount"], "0.001395000")
        self.assertEqual((result["priced_photos"], result["total_photos"]), (1, 4))

    def test_unavailable_usage_is_not_zero(self):
        for photos in ([], [{}], [returned_photo(None)], [returned_photo(model="unknown")]):
            with self.subTest(photos=photos):
                self.assertIsNone(estimate_cost(photos, "batch")["amount"])

    def test_reported_zero_usage_is_a_valid_zero_estimate(self):
        result = estimate_cost([returned_photo(0, 0)], "batch")
        self.assertEqual(result["amount"], "0.000000000")
        self.assertEqual(result["priced_photos"], 1)

    def test_malformed_token_counts_do_not_produce_estimates(self):
        for value in (True, -1, "100", 3.5, float("nan")):
            with self.subTest(value=value):
                self.assertIsNone(estimate_cost([returned_photo(value)], "batch")["amount"])

    def test_cache_usage_does_not_lower_uncached_input_estimate(self):
        photo = returned_photo()
        photo["extraction"]["usage"]["prompt_tokens_details"] = {"cached_tokens": 2174}
        self.assertEqual(estimate_cost([photo], "realtime")["amount"], "0.002790000")

    def test_returned_pro_model_is_priced_without_triggering_model_call(self):
        result = estimate_cost([returned_photo(model="mimo-v2.6-pro")], "batch")
        self.assertEqual(result["amount"], "0.004185000")

    @override_settings(MIMO_COST_PRICING_REGION="")
    def test_unconfigured_region_disables_cny_estimates(self):
        self.assertIsNone(estimate_cost([returned_photo()], "batch")["amount"])


@override_settings(MIMO_COST_PRICING_REGION="cn")
class IntakeCostAPITests(TestCase):
    def test_detail_returns_dated_estimate_separately_from_provider_usage(self):
        admin = get_user_model().objects.create_user("cost_admin", is_staff=True)
        intake = BookIntake.objects.create(user=admin, mode="batch")
        extraction = returned_photo()["extraction"]
        IntakePhoto.objects.create(intake=intake, sha256="a" * 64, extraction=extraction)
        client = APIClient()
        client.force_authenticate(admin)
        response = client.get(reverse("intake-detail", kwargs={"intake_id": intake.id}))
        self.assertEqual(response.status_code, 200)
        result = response.data["intake"]
        self.assertEqual(result["photos"][0]["extraction"]["usage"], extraction["usage"])
        self.assertEqual(result["cost_estimate"]["amount"], "0.001395000")
        self.assertEqual(result["cost_estimate"]["currency"], "CNY")
        self.assertEqual(result["cost_estimate"]["pricing_date"], "2026-09-29")
        self.assertEqual(result["cost_estimate"]["assumption"], "uncached_input")
