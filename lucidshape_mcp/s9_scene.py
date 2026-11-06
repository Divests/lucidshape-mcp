"""Deprecated import alias; use optical_scene."""
import sys
from . import optical_scene
sys.modules[__name__] = optical_scene
