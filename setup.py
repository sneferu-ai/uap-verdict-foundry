"""Setuptools compatibility entry point.

Canonical metadata lives in ``pyproject.toml``.  Keeping this shim free of
duplicate metadata prevents the two sources from silently drifting.
"""
from setuptools import setup

setup()
