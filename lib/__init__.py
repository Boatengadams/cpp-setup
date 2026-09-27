"""Cross-platform C++ environment bootstrapper.

A single ``cpp`` command that installs and configures a MinGW-w64 C++
toolchain, wires it into the system PATH, verifies it globally, installs
and configures Visual Studio Code (compiler packs, formatter, error lens and
auto-complete tooling), and finally builds + runs a C++ program that greets
the user by name.

The package is pure standard library and supports Python 3.8+.
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
