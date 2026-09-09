"""Synthetic, obfuscated RTF fixtures for format adapter tests."""

from pathlib import Path


RTF = rb"""{\rtf1\ansi\ansicpg1252\uc1\deff0
{\fonttbl{\f0\fnil Zorvak;}}
{\info{\title Qelm Archive}}
\viewkind4\pard
\b Chapter 1: Velnor Gate\b0\par
Zorvak qelmin draves the ulmor lattice.\par
The token \'e9 remains visible.\par
\page
\b Chapter 2\b0\par
Nerith solven tracks the varkel signal.\par
Unicode: \u35793?\u25991?.\par
}
"""


def make_rtf(path: Path, *, content: bytes = RTF) -> Path:
    path.write_bytes(content)
    return path
