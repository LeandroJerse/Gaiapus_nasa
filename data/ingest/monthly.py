import math
from collections import defaultdict


def monthly_rain_report(rows, reference, tolerance_mm):
    """Compare daily rain (power) against a reference, grouped by YYYY-MM."""
    by_month = defaultdict(list)
    for row in rows:
        day = row["day_utc"][:10]
        by_month[day[:7]].append((day, row["rain_mm"], reference.get(day)))

    report = {}
    for month, days in sorted(by_month.items()):
        pairs = [(p, r) for _, p, r in days if p is not None and r is not None]
        diffs = [p - r for p, r in pairs]
        missing_power = sorted(d for d, p, _ in days if p is None)
        missing_reference = sorted(d for d, _, r in days if r is None)
        mismatched = sum(abs(d) > tolerance_mm + 1e-12 for d in diffs)
        n = len(pairs)

        if n < len(days):
            status = "incomplete"
        elif mismatched:
            status = "discrepancy"
        else:
            status = "complete_match"

        report[month] = {
            "expected_days": len(days),
            "paired_days": n,
            "missing_power_days": len(missing_power),
            "missing_reference_days": len(missing_reference),
            "missing_power_dates": missing_power,
            "missing_reference_dates": missing_reference,
            "mismatched_days": mismatched,
            "power_mm": math.fsum(p for p, _ in pairs) if n else None,
            "reference_mm": math.fsum(r for _, r in pairs) if n else None,
            "diff_mm": math.fsum(diffs) if n else None,
            "mae_mm": math.fsum(map(abs, diffs)) / n if n else None,
            "bias_mm": math.fsum(diffs) / n if n else None,
            "max_abs_mm": max(map(abs, diffs)) if n else None,
            "status": status,
        }
    return report
