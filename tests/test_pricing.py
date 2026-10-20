import pytest

from cloudtrim.pricing import HOURS_PER_MONTH, make_pricing


def test_monthly_math():
    p = make_pricing("us-east-1")
    # 1000m of CPU and 1024Mi of memory for one month
    expected = (p.vcpu_hour + p.gib_hour) * HOURS_PER_MONTH
    assert p.monthly(1000, 1024) == pytest.approx(expected)
    assert p.monthly(0, 0) == 0


def test_zero_usage_is_free():
    p = make_pricing("ap-south-1")
    assert p.hourly(0, 0) == 0


def test_regions_differ():
    assert make_pricing("us-east-1").vcpu_hour != make_pricing("ap-south-1").vcpu_hour


def test_pricing_file_override(tmp_path):
    f = tmp_path / "rates.json"
    f.write_text('{"vcpu_hour": 0.10, "gib_hour": 0.02, "as_of": "2026-01"}')
    p = make_pricing("us-east-1", str(f))
    assert p.vcpu_hour == 0.10
    assert p.gib_hour == 0.02
    assert p.as_of == "2026-01"
