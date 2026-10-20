"""Rough pricing for turning waste into dollars.

These are on-demand list-price approximations, not a bill. Real spend
depends on instance family, reservations, savings plans, and what else
runs on the nodes. Treat every number here as a conversation starter
and use --pricing-file when your own numbers matter.
"""

import json
from dataclasses import dataclass

AS_OF = "2026-10"
HOURS_PER_MONTH = 730


@dataclass(frozen=True)
class Rates:
    region: str
    as_of: str
    vcpu_hour: float  # USD per vCPU-hour
    gib_hour: float  # USD per GiB-hour


# General-purpose on-demand Linux list price, mid-2026, per vCPU and GiB.
BUILTINS = {
    "us-east-1": Rates("us-east-1", AS_OF, 0.0512, 0.0128),
    "ap-south-1": Rates("ap-south-1", AS_OF, 0.0560, 0.0140),
}


def load(region, path=None):
    if path is not None:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return Rates(
            region=region,
            as_of=str(data.get("as_of", AS_OF)),
            vcpu_hour=float(data["vcpu_hour"]),
            gib_hour=float(data["gib_hour"]),
        )
    key = region if region in BUILTINS else "us-east-1"
    rates = BUILTINS[key]
    return Rates(region=region, as_of=rates.as_of,
                 vcpu_hour=rates.vcpu_hour, gib_hour=rates.gib_hour)


@dataclass
class Pricing:
    region: str
    as_of: str
    vcpu_hour: float
    gib_hour: float

    def hourly(self, cpu_m, mem_mi):
        return cpu_m / 1000 * self.vcpu_hour + mem_mi / 1024 * self.gib_hour

    def monthly(self, cpu_m, mem_mi):
        return self.hourly(cpu_m, mem_mi) * HOURS_PER_MONTH


def make_pricing(region, path=None):
    rates = load(region, path)
    return Pricing(region=rates.region, as_of=rates.as_of,
                   vcpu_hour=rates.vcpu_hour, gib_hour=rates.gib_hour)
