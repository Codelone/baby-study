"""Dated domestic price estimates for returned photo usage, never a provider bill."""

from decimal import Decimal

from django.conf import settings

PRICE_SOURCE = "https://mimo.mi.com/docs/zh-CN/quick-start/usage-guide/text-generation/batch-api"
PRICE_DATE = "2026-09-29"
# CNY per million tokens, uncached input / output; checked against MiMo's domestic table.
RATES = {
    "mimo-v2.6-flash": (Decimal("1"), Decimal("2")),
    "mimo-v2.6-pro": (Decimal("3"), Decimal("6")),
}


def estimate_cost(photos, mode):
    estimate = {
        "currency": "CNY",
        "amount": None,
        "priced_photos": 0,
        "total_photos": len(photos),
        "pricing_date": PRICE_DATE,
        "source_url": PRICE_SOURCE,
        "assumption": "uncached_input",
        "mode": mode,
    }
    # Unknown account region/model/usage must not be represented as a zero-cost call.
    if settings.MIMO_COST_PRICING_REGION != "cn" or mode not in {"batch", "realtime"}:
        return estimate
    factor = Decimal("0.5") if mode == "batch" else Decimal("1")
    amount = Decimal("0")
    for photo in photos:
        extraction = photo.get("extraction") or {}
        model = extraction.get("model_name")
        rates = RATES.get(model) if isinstance(model, str) else None
        usage = extraction.get("usage")
        if not rates or not isinstance(usage, dict):
            continue
        inputs, outputs = usage.get("prompt_tokens"), usage.get("completion_tokens")
        if any(type(value) is not int or value < 0 for value in (inputs, outputs)):
            continue
        amount += (Decimal(inputs) * rates[0] + Decimal(outputs) * rates[1]) * factor
        estimate["priced_photos"] += 1
    if estimate["priced_photos"]:
        estimate["amount"] = format(
            (amount / Decimal("1000000")).quantize(Decimal("0.000000001")), "f"
        )
    return estimate
