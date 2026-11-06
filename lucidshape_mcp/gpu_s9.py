"""Deprecated import alias; use gpu_trace."""
import sys
from . import gpu_trace
sys.modules[__name__] = gpu_trace
