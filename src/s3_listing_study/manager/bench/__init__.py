"""Benchmark planning: what to run against a bucket, and with what allocation.

Manager-side only. Nothing here ships in a derived image — a plan is resolved
before any attempt task is submitted, and what reaches the worker is one already
resolved case.
"""
