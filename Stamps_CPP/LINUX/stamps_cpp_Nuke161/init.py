"""
init.py - Stamps C++ plugin initialization

Place this file in ~/.nuke/stamps_cpp/ (or wherever stamps_cpp.py lives).
The compiled .so can live anywhere on NUKE_PATH / pluginPath.
"""
import os
import sys
import nuke

# Add the stamps_cpp directory to Nuke's plugin path
stamps_dir = os.path.dirname(__file__)
nuke.pluginAddPath(stamps_dir)

# Also add icons subdirectory
icons_dir = os.path.join(stamps_dir, "icons")
if os.path.isdir(icons_dir):
    nuke.pluginAddPath(icons_dir)

# ---------------------------------------------------------------
# Preload the multi-class C++ plugin.
#
# The .so contains four Op classes (StampAnchor, StampWired,
# StampDeepAnchor, StampDeepWired) but the file is named
# StampsCpp.so — Nuke's auto-discovery only matches filenames
# to class names, so it can't find "StampAnchor" inside
# "StampsCpp.so".
#
# ctypes.CDLL just dlopen's the library with no name check.
# The static Op::Description constructors register all four
# classes with Nuke's Op registry on load.
# ---------------------------------------------------------------
def _preload_stamps_plugin():
    import ctypes

    if sys.platform == "win32":
        ext = ".dll"
    elif sys.platform == "darwin":
        ext = ".dylib"
    else:
        ext = ".so"

    lib_name = "StampsCpp" + ext

    # Search everywhere Nuke searches: pluginPath + this directory
    search_dirs = list(nuke.pluginPath()) + [stamps_dir]

    for d in search_dirs:
        so_path = os.path.join(d, lib_name)
        if os.path.isfile(so_path):
            try:
                ctypes.CDLL(so_path, ctypes.RTLD_GLOBAL)
                return
            except OSError as e:
                nuke.tprint("[Stamps C++] Failed to load %s: %s" % (so_path, e))
                return

    nuke.tprint("[Stamps C++] %s not found on pluginPath. "
                "Compile and place it on NUKE_PATH." % lib_name)

_preload_stamps_plugin()
