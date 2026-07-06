"""Adapters that map AttackPlan steps to existing DAH_SMU lab agents."""

from .jammer_adapter import JammerAdapter
from .mavlink_injector import MavlinkInjectorAdapter
from .tamper_adapter import TamperAdapter


def default_adapters():
    return [JammerAdapter(), MavlinkInjectorAdapter(), TamperAdapter()]
