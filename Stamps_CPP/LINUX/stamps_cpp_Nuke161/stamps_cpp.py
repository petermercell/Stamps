"""
stamps_cpp.py - Python companion for Stamps C++ NDK plugin

Handles UI panels (AnchorSelector, NewAnchorPanel), complex reconnection
logic that requires full node-graph traversal, and menu/shortcut setup.

The C++ side handles: pass-through engine, knob definitions, basic
knob_changed callbacks, and GPU pass-through.

The Python side handles: dialogs, node creation workflows, cross-node
iteration, and operations that require nuke.toNode() / nuke.allNodes().

Original concept: Adrian Pueyo and Alexey Kuchinski (BSD-2-Clause)
C++ port by Peter Mercell
"""

import nuke
import nukescripts
import re
import sys
from functools import partial

# Python 3 compat
if sys.version_info[0] >= 3:
    unicode = str

# PySide import
try:
    from PySide6 import QtWidgets, QtCore
except ImportError:
    try:
        from PySide2 import QtWidgets, QtCore
    except ImportError:
        from PySide import QtCore, QtGui as QtWidgets

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
try:
    from stamps_cpp_config import *
except Exception:
    pass

VERSION = "v2.0-cpp"
STAMPS_SHORTCUT = "F8"
STAMPS_HELP = (
    "Stamps C++ by Peter Mercell.\n"
    "Based on Stamps by Adrian Pueyo and Alexey Kuchinski.\n"
    "GPU-accelerated pass-through. Native C++ callbacks."
)

# Node class names (must match C++ registration)
CLASS_ANCHOR = "StampAnchor"
CLASS_WIRED = "StampWired"
CLASS_DEEP_ANCHOR = "StampDeepAnchor"
CLASS_DEEP_WIRED = "StampDeepWired"

# All C++ stamp class names for efficient lookup
ALL_ANCHOR_CLASSES = [CLASS_ANCHOR, CLASS_DEEP_ANCHOR]
ALL_WIRED_CLASSES = [CLASS_WIRED, CLASS_DEEP_WIRED]

# Node types that use native C++ ops
CPP_TYPES = {"2D", "Deep"}

# Node types that fall back to NoOp (like original Stamps)
# Camera, Axis, 3D, Particle: NoOp is a universal passthrough for these
NOOP_TYPES = {"3D", "Camera", "Axis", "Particle"}

# Map from node type to stamp classes (C++ types only)
ANCHOR_CLASS_MAP = {
    "2D": CLASS_ANCHOR,
    "Deep": CLASS_DEEP_ANCHOR,
}

WIRED_CLASS_MAP = {
    "2D": CLASS_WIRED,
    "Deep": CLASS_DEEP_WIRED,
}

# For backward compat with Python stamps
LEGACY_IDENTIFIER_ANCHOR = "anchor"
LEGACY_IDENTIFIER_WIRED = "wired"

# Classes that shouldn't get stamps
NodeExceptionClasses = ["Viewer"]

# Global state
_Stamps_LastCreated = None
_Stamps_LockCallbacks = False

# =========================================================================
# DISCOVERY FUNCTIONS (fast: uses nuke.allNodes with class filter)
# =========================================================================

def allAnchors(selection=None):
    """Return all anchor nodes (2D + Deep C++ classes + legacy)."""
    nodes = []
    for cls in ALL_ANCHOR_CLASSES:
        nodes.extend(nuke.allNodes(cls))
    if selection is not None:
        nodes = [n for n in nodes if n in selection]
    # Also find legacy Python stamps (NoOp with identifier=anchor)
    for n in nuke.allNodes("NoOp"):
        if n.knob("identifier") and n["identifier"].value() == LEGACY_IDENTIFIER_ANCHOR:
            if selection is None or n in selection:
                nodes.append(n)
    return nodes


def allWireds(selection=None):
    """Return all wired stamp nodes (2D + Deep C++ classes + legacy)."""
    nodes = []
    for cls in ALL_WIRED_CLASSES:
        nodes.extend(nuke.allNodes(cls))
    if selection is not None:
        nodes = [n for n in nodes if n in selection]
    # Also find legacy Python stamps
    for n in nuke.allNodes("NoOp"):
        if n.knob("identifier") and n["identifier"].value() == LEGACY_IDENTIFIER_WIRED:
            if selection is None or n in selection:
                nodes.append(n)
    return nodes


def isAnchor(node):
    """Check if a node is any kind of anchor (C++ 2D, Deep, or legacy)."""
    if not node:
        return False
    if node.Class() in ALL_ANCHOR_CLASSES:
        return True
    # Legacy check
    try:
        return (node.knob("identifier") and
                node["identifier"].value() == LEGACY_IDENTIFIER_ANCHOR)
    except:
        return False


def isWired(node):
    """Check if a node is any kind of wired stamp (C++ 2D, Deep, or legacy)."""
    if not node:
        return False
    if node.Class() in ALL_WIRED_CLASSES:
        return True
    try:
        return (node.knob("identifier") and
                node["identifier"].value() == LEGACY_IDENTIFIER_WIRED)
    except:
        return False


def totalAnchors(selection=None):
    return len(allAnchors(selection))


def anchorWireds(anchor):
    """Find all wired stamps connected to a specific anchor."""
    if not anchor:
        return []
    a_name = anchor.name()
    result = []
    for w in allWireds():
        if w.knob("anchor") and w["anchor"].value() == a_name:
            result.append(w)
    return result


def allTags(selection=None):
    """Collect all unique tags from all anchors."""
    tags = set()
    for a in allAnchors(selection):
        try:
            t = a["tags"].value().strip()
            for tag in re.split(r" *, *", t):
                tag = tag.strip()
                if tag:
                    tags.add(tag)
        except:
            pass
    return sorted(tags, key=str.lower)


def findAnchorsByTitle(title, selection=None):
    """Find all anchors with a matching title."""
    if not title:
        return []
    result = []
    for a in allAnchors(selection):
        try:
            if a.knob("title") and a["title"].value() == title:
                result.append(a)
        except:
            pass
    return result


def stampCount(anchor_name):
    """Count wired stamps connected to a given anchor name."""
    count = 0
    for w in allWireds():
        try:
            if w.knob("anchor") and w["anchor"].value() == anchor_name:
                count += 1
        except:
            pass
    return count


# =========================================================================
# UTILITY FUNCTIONS
# =========================================================================

def titleIsLegal(title):
    return bool(title and title.strip())


def realInput(node, stopOnLabel=False, mode=""):
    """Walk upstream past Dots, NoOps, Stamps to find the real source."""
    IgnoreClasses = ["NoOp", "Dot", "Reformat", "DeepReformat", "Crop",
                     CLASS_ANCHOR, CLASS_WIRED, CLASS_DEEP_ANCHOR, CLASS_DEEP_WIRED]
    try:
        n = node
        if (isAnchor(n) or isWired(n) or n.Class() in IgnoreClasses):
            if stopOnLabel and n.knob("label") and n["label"].value().strip():
                return n
            if n.input(0):
                return realInput(n.input(0), stopOnLabel, mode)
        return n
    except:
        return node


def nodeType(n):
    """Determine the stream type of a node."""
    DeepExceptions = ["DeepToImage", "DeepHoldout", "DeepHoldout2"]
    ParticleExceptions = ["ParticleToImage"]
    try:
        cls = n.Class()
    except:
        return "2D"
    if cls.startswith("Deep") and cls not in DeepExceptions:
        return "Deep"
    if cls.startswith("Particle") and cls not in ParticleExceptions:
        return "Particle"
    if cls.startswith("ScanlineRender") or cls.startswith("RayRender"):
        return "2D"
    # Camera variants (including USD cameras)
    if cls.startswith("Camera"):
        return "Camera"
    # Axis variants
    if cls.startswith("Axis"):
        return "Axis"
    # USD / new 3D system
    if cls.startswith("Usd") or cls.startswith("USD"):
        return "3D"
    # Traditional 3D: GeoOps have render_mode + display knobs
    if (n.knob("render_mode") and n.knob("display")) or cls in [
            "GeoNoOp", "EditGeo", "TransformGeo", "MergeGeo",
            "Scene", "ReadGeo", "ReadGeo2", "WriteGeo"]:
        return "3D"
    return "2D"


def canOutputDeep(node):
    """Check if a node classified as 2D can also output deep data.
    Nodes like ScanlineRender and RayRender can produce both."""
    try:
        cls = node.Class()
    except:
        return False
    # Render nodes that support deep output
    if cls in ("ScanlineRender", "ScanlineRender2", "RayRender"):
        return True
    # Generic check: any node with a deep output knob
    for knob_name in ("deep_output_mode", "generate_deep_data", "deep_output"):
        if node.knob(knob_name):
            return True
    return False


def getDefaultTitle(node=None):
    """Guess a good title for a new stamp based on the source node."""
    if node is None:
        return "Stamp"
    try:
        # Use the file knob if it exists (Read nodes)
        if node.knob("file"):
            import os
            fname = os.path.basename(node["file"].value())
            name = os.path.splitext(fname)[0] if fname else ""
            if name:
                # Clean up common patterns
                name = re.sub(r'_v\d+$', '', name)
                name = re.sub(r'\.\d+$', '', name)
                return name
        # Use label if available
        if node.knob("label") and node["label"].value().strip():
            label = node["label"].value().strip()
            # Strip HTML
            label = re.sub(r'<[^>]+>', '', label)
            if label:
                return label
        # Fall back to node name
        return node.name()
    except:
        return "Stamp"


def backdropTags(node):
    """Get labels of backdrops containing the node."""
    if not node:
        return []
    x, y = node.xpos(), node.ypos()
    w, h = node.screenWidth(), node.screenHeight()
    tags = []
    for b in nuke.allNodes("BackdropNode"):
        try:
            bx = int(b['xpos'].value())
            by = int(b['ypos'].value())
            br = int(b['bdwidth'].value()) + bx
            bt = int(b['bdheight'].value()) + by
            if x >= bx and (x + w) <= br and y > by and (y + h) <= bt:
                label = b['label'].value().strip()
                if label:
                    # Strip HTML tags
                    label = re.sub(r'<[^>]+>', '', label).strip()
                    if label:
                        tags.append(label)
        except:
            continue
    return tags


# =========================================================================
# STAMP CREATION
# =========================================================================

def createAnchor(title="", tags="", input_node=None, node_type="2D"):
    """Create an anchor stamp node.
    2D/Deep: native C++ ops.  3D/Camera/Axis/Particle: NoOp with Python knobs."""
    if node_type in NOOP_TYPES:
        return _createNoOpAnchor(title, tags, input_node, node_type)

    cls = ANCHOR_CLASS_MAP.get(node_type, CLASS_ANCHOR)
    n = nuke.createNode(cls, inpanel=False)
    n["title"].setValue(title)
    n["tags"].setValue(tags)
    n["tile_color"].setValue(0xFFFFFF01)
    n["note_font_size"].setValue(20)
    n["label"].setValue("[value title]")
    if input_node:
        n.setInput(0, input_node)
    return n


def createWired(anchor, node_type="2D"):
    """Create a wired stamp node connected to the anchor.
    2D/Deep: native C++ ops.  3D/Camera/Axis/Particle: NoOp with Python knobs."""
    global _Stamps_LastCreated
    _Stamps_LastCreated = anchor.name()

    if node_type in NOOP_TYPES:
        return _createNoOpWired(anchor, node_type)

    cls = WIRED_CLASS_MAP.get(node_type, CLASS_WIRED)
    n = nuke.createNode(cls, inpanel=False)
    n["title"].setValue(anchor["title"].value())
    n["anchor"].setValue(anchor.name())
    n["tile_color"].setValue(0x01000001)
    n["note_font_size"].setValue(20)
    n["hide_input"].setValue(True)
    n["label"].setValue("[value title]")

    # Position near the anchor
    x, y = n.xpos(), n.ypos()
    nw = n.screenWidth()
    aw = anchor.screenWidth()
    n.setInput(0, anchor)
    n["xpos"].setValue(x - nw // 2 + aw // 2)
    n["ypos"].setValue(y)

    # Update tags display
    _updateWiredTagsDisplay(n)
    return n


# ---------------------------------------------------------------
# NoOp-based stamps for 3D / Camera / Axis / Particle
# Same approach as original Stamps by Adrian Pueyo — NoOp is a
# universal passthrough that works for any data type in Nuke.
# ---------------------------------------------------------------

def _createNoOpAnchor(title, tags, input_node, node_type):
    """Create a NoOp-based anchor for 3D/Camera/Axis/Particle streams."""
    n = nuke.createNode("NoOp", inpanel=False)

    # Identifier (used by allAnchors/isAnchor for detection)
    k = nuke.String_Knob("identifier", "identifier")
    k.setValue(LEGACY_IDENTIFIER_ANCHOR)
    k.setFlag(nuke.INVISIBLE)
    n.addKnob(k)

    k = nuke.String_Knob("title", "Title:")
    k.setValue(title)
    k.setTooltip(
        "Displayed name on the Node Graph.\n"
        "This is for display only, different from the node name.")
    n.addKnob(k)

    k = nuke.String_Knob("prev_title", "prev_title")
    k.setValue(title)
    k.setFlag(nuke.INVISIBLE)
    n.addKnob(k)

    k = nuke.String_Knob("prev_name", "prev_name")
    k.setValue(n.name())
    k.setFlag(nuke.INVISIBLE)
    n.addKnob(k)

    k = nuke.Int_Knob("showing", "showing")
    k.setValue(0)
    k.setFlag(nuke.INVISIBLE)
    n.addKnob(k)

    k = nuke.String_Knob("tags", "Tags")
    k.setValue(tags)
    k.setTooltip("Comma-separated tags for the Stamp Selector.")
    n.addKnob(k)

    n.addKnob(nuke.Text_Knob("divider1", ""))

    k = nuke.Text_Knob("stamps_label", "Stamps:", "")
    n.addKnob(k)

    k = nuke.PyScript_Knob(
        "createStamp", "new",
        "import stamps_cpp; stamps_cpp.stampCreateWiredFromAnchor(nuke.thisNode())")
    k.clearFlag(nuke.STARTLINE)
    n.addKnob(k)

    k = nuke.PyScript_Knob(
        "selectStamps", "select",
        "import stamps_cpp; stamps_cpp.anchorSelectWireds(nuke.thisNode())")
    k.clearFlag(nuke.STARTLINE)
    n.addKnob(k)

    k = nuke.PyScript_Knob(
        "reconnectStamps", "reconnect",
        "import stamps_cpp; stamps_cpp.anchorReconnectWireds(nuke.thisNode())")
    k.clearFlag(nuke.STARTLINE)
    n.addKnob(k)

    k = nuke.PyScript_Knob(
        "zoomNext", "zoom next",
        "import stamps_cpp; stamps_cpp.wiredZoomNext(nuke.thisNode().name())")
    k.clearFlag(nuke.STARTLINE)
    n.addKnob(k)

    # Style
    n["tile_color"].setValue(0xFFFFFF01)
    n["note_font_size"].setValue(20)
    n["label"].setValue("[value title]")
    if input_node:
        n.setInput(0, input_node)
    return n


def _createNoOpWired(anchor, node_type):
    """Create a NoOp-based wired stamp for 3D/Camera/Axis/Particle streams."""
    n = nuke.createNode("NoOp", inpanel=False)

    # Identifier
    k = nuke.String_Knob("identifier", "identifier")
    k.setValue(LEGACY_IDENTIFIER_WIRED)
    k.setFlag(nuke.INVISIBLE)
    n.addKnob(k)

    k = nuke.Boolean_Knob("lockCallbacks", "")
    k.setValue(False)
    k.setFlag(nuke.INVISIBLE)
    n.addKnob(k)

    k = nuke.Boolean_Knob("toReconnect", "")
    k.setValue(False)  # Already connected, no need to reconnect
    k.setFlag(nuke.INVISIBLE)
    n.addKnob(k)

    k = nuke.String_Knob("title", "Title:")
    k.setValue(anchor["title"].value())
    n.addKnob(k)

    k = nuke.String_Knob("prev_title", "prev_title")
    k.setValue(anchor["title"].value())
    k.setFlag(nuke.INVISIBLE)
    n.addKnob(k)

    k = nuke.String_Knob("tags", "Tags:")
    k.setFlag(nuke.DISABLED)
    n.addKnob(k)

    k = nuke.String_Knob("backdrops", "Backdrops:")
    k.setFlag(nuke.DISABLED)
    n.addKnob(k)

    n.addKnob(nuke.Text_Knob("divider1", ""))

    # Anchor name reference
    k = nuke.String_Knob("anchor", "Anchor:")
    k.setValue(anchor.name())
    n.addKnob(k)

    # Buttons - Anchor
    k = nuke.Text_Knob("anchor_label", "Anchor:", "")
    n.addKnob(k)

    k = nuke.PyScript_Knob(
        "show_anchor", " show anchor ",
        "import stamps_cpp; stamps_cpp.wiredShowAnchor(nuke.thisNode())")
    k.clearFlag(nuke.STARTLINE)
    n.addKnob(k)

    k = nuke.PyScript_Knob(
        "zoom_anchor", "zoom anchor",
        "import stamps_cpp; stamps_cpp.wiredZoomAnchor(nuke.thisNode())")
    k.clearFlag(nuke.STARTLINE)
    n.addKnob(k)

    # Buttons - Stamps
    k = nuke.Text_Knob("stamps_label", "Stamps:", "")
    n.addKnob(k)

    k = nuke.PyScript_Knob(
        "zoomNext", " zoom next ",
        "import stamps_cpp; stamps_cpp.wiredZoomNext()")
    k.clearFlag(nuke.STARTLINE)
    n.addKnob(k)

    k = nuke.PyScript_Knob(
        "selectSimilar", " select similar ",
        "import stamps_cpp; stamps_cpp.wiredSelectSimilar()")
    k.clearFlag(nuke.STARTLINE)
    n.addKnob(k)

    # Buttons - Reconnect
    k = nuke.Text_Knob("reconnect_label", "Reconnect:", "")
    n.addKnob(k)

    k = nuke.PyScript_Knob(
        "reconnect_this", "this",
        "import stamps_cpp; stamps_cpp.wiredReconnect(nuke.thisNode())")
    k.clearFlag(nuke.STARTLINE)
    n.addKnob(k)

    k = nuke.PyScript_Knob(
        "reconnect_similar", "similar",
        "import stamps_cpp; stamps_cpp.wiredReconnectSimilar()")
    k.clearFlag(nuke.STARTLINE)
    n.addKnob(k)

    k = nuke.PyScript_Knob(
        "reconnect_all", "all",
        "import stamps_cpp; stamps_cpp.wiredReconnectAll()")
    k.clearFlag(nuke.STARTLINE)
    n.addKnob(k)

    # Style and connect
    n["tile_color"].setValue(0x01000001)
    n["note_font_size"].setValue(20)
    n["hide_input"].setValue(True)
    n["label"].setValue("[value title]")

    x, y = n.xpos(), n.ypos()
    nw = n.screenWidth()
    aw = anchor.screenWidth()
    n.setInput(0, anchor)
    n["xpos"].setValue(x - nw // 2 + aw // 2)
    n["ypos"].setValue(y)

    _updateWiredTagsDisplay(n)
    return n


# =========================================================================
# BUTTON CALLBACKS (called from C++ PyScript_knob)
# =========================================================================

def wiredShowAnchor(n=None):
    """Show the properties panel for the anchor."""
    if n is None:
        n = nuke.thisNode()
    a_name = n["anchor"].value()
    if nuke.exists(a_name):
        nuke.show(nuke.toNode(a_name))
    elif n.inputs():
        nuke.show(n.input(0))


def wiredZoomAnchor(n=None):
    """Zoom the DAG view to the anchor node."""
    if n is None:
        n = nuke.thisNode()
    a_name = n["anchor"].value()
    target = None
    if nuke.exists(a_name):
        target = nuke.toNode(a_name)
    elif n.inputs():
        target = n.input(0)
    if target:
        center = [target.xpos() + target.screenWidth() / 2,
                  target.ypos() + target.screenHeight() / 2]
        nuke.zoom(nuke.zoom(), center)


def wiredZoomNext(anchor_name=None):
    """Cycle through wired stamps of the same anchor."""
    n = nuke.thisNode()
    if anchor_name is None:
        if n.knob("anchor"):
            anchor_name = n["anchor"].value()
        else:
            anchor_name = n.name()

    wireds = [w for w in allWireds()
              if w.knob("anchor") and w["anchor"].value() == anchor_name]
    if not wireds:
        return

    wireds.sort(key=lambda w: w.name())

    # Find current position in list
    current_name = n.name()
    idx = -1
    for i, w in enumerate(wireds):
        if w.name() == current_name:
            idx = i
            break

    # Zoom to next
    next_idx = (idx + 1) % len(wireds)
    target = wireds[next_idx]
    center = [target.xpos() + target.screenWidth() / 2,
              target.ypos() + target.screenHeight() / 2]
    nuke.zoom(nuke.zoom(), center)


def wiredSelectSimilar(anchor_name=None):
    """Select all wired stamps sharing the same anchor."""
    n = nuke.thisNode()
    if anchor_name is None:
        if n.knob("anchor"):
            anchor_name = n["anchor"].value()
        else:
            anchor_name = n.name()

    for w in allWireds():
        try:
            if w.knob("anchor") and w["anchor"].value() == anchor_name:
                w.setSelected(True)
        except:
            pass


def wiredReconnect(n=None):
    """Reconnect a single wired stamp to its anchor."""
    if n is None:
        n = nuke.thisNode()
    a_name = n["anchor"].value()
    if nuke.exists(a_name):
        a = nuke.toNode(a_name)
        n.setInput(0, a)
        _setWiredStyle(n, 0)
    else:
        _setWiredStyle(n, 1)


def wiredReconnectSimilar(anchor_name=None):
    """Reconnect all stamps sharing the same anchor."""
    n = nuke.thisNode()
    if anchor_name is None:
        anchor_name = n["anchor"].value()
    if not nuke.exists(anchor_name):
        return
    a = nuke.toNode(anchor_name)
    for w in allWireds():
        if w.knob("anchor") and w["anchor"].value() == anchor_name:
            w.setInput(0, a)
            _setWiredStyle(w, 0)


def wiredReconnectAll():
    """Reconnect ALL wired stamps to their respective anchors."""
    for w in allWireds():
        try:
            a_name = w["anchor"].value()
            if nuke.exists(a_name):
                w.setInput(0, nuke.toNode(a_name))
                _setWiredStyle(w, 0)
            else:
                _setWiredStyle(w, 1)
        except:
            pass


def wiredReconnectByTitle(title=None):
    """Reconnect this stamp by finding an anchor with matching title."""
    n = nuke.thisNode()
    if title is None:
        title = n["title"].value()
    anchors = findAnchorsByTitle(title)
    if anchors:
        a = anchors[0]
        n.setInput(0, a)
        n["anchor"].setValue(a.name())
        _setWiredStyle(n, 0)
    else:
        nuke.message("No anchor found with title: " + title)


def wiredReconnectByTitleSimilar(title=None):
    """Reconnect this stamp and siblings by title."""
    n = nuke.thisNode()
    if title is None:
        title = n["title"].value()
    anchors = findAnchorsByTitle(title)
    if not anchors:
        nuke.message("No anchor found with title: " + title)
        return
    a = anchors[0]
    a_old = n["anchor"].value()
    for w in allWireds():
        if w.knob("anchor") and w["anchor"].value() == a_old:
            w.setInput(0, a)
            w["anchor"].setValue(a.name())
            _setWiredStyle(w, 0)


def wiredReconnectByTitleSelected():
    """Reconnect all selected stamps by title."""
    for n in nuke.selectedNodes():
        if isWired(n):
            title = n["title"].value()
            anchors = findAnchorsByTitle(title)
            if anchors:
                n.setInput(0, anchors[0])
                n["anchor"].setValue(anchors[0].name())
                _setWiredStyle(n, 0)


def wiredReconnectBySelection():
    """Reconnect this stamp to a selected anchor."""
    n = nuke.thisNode()
    sel = nuke.selectedNodes()
    anchor = None
    for s in sel:
        if isAnchor(s) and s != n:
            anchor = s
            break
    if anchor:
        n.setInput(0, anchor)
        n["anchor"].setValue(anchor.name())
        n["title"].setValue(anchor["title"].value())
        _setWiredStyle(n, 0)
    else:
        nuke.message("Please select an Anchor node.")


def wiredReconnectBySelectionSimilar():
    """Reconnect similar stamps to a selected anchor."""
    n = nuke.thisNode()
    sel = nuke.selectedNodes()
    anchor = None
    for s in sel:
        if isAnchor(s) and s != n:
            anchor = s
            break
    if not anchor:
        nuke.message("Please select an Anchor node.")
        return
    old_anchor = n["anchor"].value()
    for w in allWireds():
        if w.knob("anchor") and w["anchor"].value() == old_anchor:
            w.setInput(0, anchor)
            w["anchor"].setValue(anchor.name())
            w["title"].setValue(anchor["title"].value())
            _setWiredStyle(w, 0)


def wiredReconnectBySelectionSelected():
    """Reconnect all selected stamps to a selected anchor."""
    sel = nuke.selectedNodes()
    anchor = None
    wireds = []
    for s in sel:
        if isAnchor(s):
            anchor = s
        elif isWired(s):
            wireds.append(s)
    if not anchor:
        nuke.message("Please select an Anchor node along with the stamps.")
        return
    for w in wireds:
        w.setInput(0, anchor)
        w["anchor"].setValue(anchor.name())
        w["title"].setValue(anchor["title"].value())
        _setWiredStyle(w, 0)


def anchorSelectWireds(anchor=None):
    """Select all wired stamps of this anchor."""
    if anchor is None:
        anchor = nuke.thisNode()
    for w in anchorWireds(anchor):
        w.setSelected(True)


def anchorReconnectWireds(anchor=None):
    """Reconnect all wired stamps to this anchor."""
    if anchor is None:
        anchor = nuke.thisNode()
    for w in anchorWireds(anchor):
        w.setInput(0, anchor)
        w["anchor"].setValue(anchor.name())
        _setWiredStyle(w, 0)


def stampCreateWiredFromAnchor(anchor=None):
    """Create a new wired stamp from a given anchor (button callback)."""
    if anchor is None:
        anchor = nuke.thisNode()
    ns = nuke.selectedNodes()
    for n in ns:
        n.setSelected(False)

    # Determine node type from anchor's class or input
    nt = "2D"
    if anchor.Class() in (CLASS_DEEP_ANCHOR,):
        nt = "Deep"
    elif anchor.Class() == "NoOp" and isAnchor(anchor):
        # NoOp-based anchor (3D/Camera/Axis/Particle)
        # Detect type from input, or default to 3D for NoOp anchors
        if anchor.input(0):
            nt = nodeType(realInput(anchor))
        else:
            nt = "3D"  # safe fallback: NoOp wired for disconnected NoOp anchor
    elif anchor.input(0):
        nt = nodeType(realInput(anchor))

    dot = nuke.nodes.Dot()
    dot.setXYpos(anchor.xpos(), anchor.ypos())
    dot.setInput(0, anchor)
    nw = createWired(anchor, node_type=nt)
    nuke.delete(dot)
    for n in ns:
        n.setSelected(True)
    nw.setXYpos(
        int(anchor.xpos() + anchor.screenWidth() / 2 - nw.screenWidth() / 2),
        anchor.ypos() + 56
    )
    anchor.setSelected(False)
    return nw


# =========================================================================
# INTERNAL CALLBACKS (called from C++ knob_changed via script_command)
# =========================================================================

def _anchorTitleChanged():
    """Handle anchor title change — propagate to wireds."""
    n = nuke.thisNode()
    title = n["title"].value()
    if not titleIsLegal(title):
        nuke.message("Please set a valid title.")
        try:
            n["title"].setValue(n["prev_title"].value())
        except:
            pass
        return
    if nuke.ask("Do you want to update the linked stamps' title?"):
        for w in anchorWireds(n):
            w["title"].setValue(title)
            w["prev_title"].setValue(title)
    else:
        try:
            n["title"].setValue(n["prev_title"].value())
        except:
            pass


def _anchorNameChanged():
    """Handle anchor rename — update anchor references in wireds."""
    n = nuke.thisNode()
    new_name = n.name()
    try:
        old_name = n["prev_name"].value()
    except:
        old_name = new_name
    if old_name and old_name != new_name:
        for w in allWireds():
            if w.knob("anchor") and w["anchor"].value() == old_name:
                w["anchor"].setValue(new_name)
    n["prev_name"].setValue(new_name)


def _anchorTagsChanged():
    """Handle anchor tags change — update display on wireds."""
    n = nuke.thisNode()
    for w in anchorWireds(n):
        _updateWiredTagsDisplay(w)


def _wiredDoReconnect():
    """Handle wired stamp reconnection on paste/create."""
    n = nuke.thisNode()
    if not n.knob("anchor"):
        return
    a_name = n["anchor"].value()

    # Try auto-reconnect by title first
    if n.knob("auto_reconnect_by_title") and n["auto_reconnect_by_title"].value():
        n["auto_reconnect_by_title"].setValue(False)
        title = n["title"].value() if n.knob("title") else ""
        for a in allAnchors():
            if a.knob("title") and a["title"].value() == title:
                n.setInput(0, a)
                n["anchor"].setValue(a.name())
                _setWiredStyle(n, 0)
                return

    # Try reconnect by stored anchor name
    if nuke.exists(a_name):
        a = nuke.toNode(a_name)
        # Verify title match
        if (a.knob("title") and n.knob("title") and
                a["title"].value() == n["title"].value()):
            n.setInput(0, a)
            _setWiredStyle(n, 0)
        else:
            _setWiredStyle(n, 1)
    else:
        _setWiredStyle(n, 1)


def _wiredTitleChanged():
    """Handle title change on a wired stamp."""
    n = nuke.thisNode()
    title = n["title"].value()
    if not titleIsLegal(title):
        nuke.message("Please set a valid title.")
        try:
            n["title"].setValue(n["prev_title"].value())
        except:
            pass
        return
    if nuke.ask("Do you want to update the linked stamps' title?"):
        # Find and retitle the anchor
        a_name = n["anchor"].value()
        if nuke.exists(a_name):
            a = nuke.toNode(a_name)
            a["title"].setValue(title)
            a["prev_title"].setValue(title)
        # Retitle all siblings
        for w in allWireds():
            if w.knob("anchor") and w["anchor"].value() == a_name:
                w["title"].setValue(title)
                w["prev_title"].setValue(title)
    else:
        try:
            n["title"].setValue(n["prev_title"].value())
        except:
            pass


def _wiredUpdateTagsDisplay():
    """Update the tags/backdrops display on a wired stamp."""
    _updateWiredTagsDisplay(nuke.thisNode())


def _updateWiredTagsDisplay(n):
    """Internal: update tags/backdrops text on a wired stamp."""
    try:
        a_name = n["anchor"].value()
        if not nuke.exists(a_name):
            return
        a = nuke.toNode(a_name)
        tags = a["tags"].value().strip().strip(",")
        bds = backdropTags(a)
        if tags:
            n["tags"].setValue(tags)
        else:
            n["tags"].setValue("")
        if bds:
            n["backdrops"].setValue(", ".join(bds))
        else:
            n["backdrops"].setValue("")
    except:
        pass


# =========================================================================
# STYLE HELPERS
# =========================================================================

def _setWiredStyle(n, style):
    """Set visual style: 0=OK, 1=broken."""
    try:
        if style == 0:
            n["note_font_size"].setValue(20)
            n["note_font_color"].setValue(0)
        else:
            n["note_font_size"].setValue(40)
            n["note_font_color"].setValue(4278190335)  # red
    except:
        pass


# =========================================================================
# ANCHOR SELECTOR PANEL
# =========================================================================

class AnchorSelector(QtWidgets.QDialog):
    """Dialog for selecting an anchor when creating a new wired stamp."""

    def __init__(self):
        super(AnchorSelector, self).__init__()
        self.setWindowTitle("Stamps: Select an Anchor.")
        self.chosen_anchors = []
        self._buildData()
        self._initUI()

    def _buildData(self):
        """Collect anchor info for display."""
        self._anchors = allAnchors()
        self._names = []
        self._titles = []
        self._tags_map = {}  # name -> [tags]
        self._bd_map = {}    # name -> [backdrops]

        for a in self._anchors:
            name = a.name()
            title = a["title"].value() if a.knob("title") else name
            self._names.append(name)
            self._titles.append(title)

            tags = []
            if a.knob("tags"):
                tags = [t.strip() for t in a["tags"].value().split(",") if t.strip()]
            self._tags_map[name] = tags

            bds = backdropTags(a)
            self._bd_map[name] = bds

        # Unique tags and backdrops
        self._all_tags = sorted(set(
            t for tags in self._tags_map.values() for t in tags
        ), key=str.lower)
        self._all_bds = sorted(set(
            b for bds in self._bd_map.values() for b in bds
        ), key=str.lower)

    def _initUI(self):
        layout = QtWidgets.QVBoxLayout()

        # Header
        header = QtWidgets.QLabel("<b>Anchor Stamp Selector</b>")
        header.setStyleSheet("font-size:14px; color:#CCC;")
        sub = QtWidgets.QLabel(
            "Select an Anchor to make a Stamp for.<br>"
            "<small style='color:#999'>Right-click OK for multi-select.</small>")
        layout.addWidget(header)
        layout.addWidget(sub)

        # Scroll area with tag sections
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_w = QtWidgets.QWidget()
        scroll_layout = QtWidgets.QVBoxLayout()
        scroll_w.setLayout(scroll_layout)
        scroll.setWidget(scroll_w)

        grid = QtWidgets.QGridLayout()
        scroll_layout.addLayout(grid)
        scroll_layout.addStretch()

        row = 0

        # By tag
        if self._all_tags:
            lbl = QtWidgets.QLabel("<i>Tags</i>")
            lbl.setStyleSheet("color:#666; padding-left:3px;")
            grid.addWidget(lbl, row, 0, 1, 3)
            row += 1

            for tag in self._all_tags:
                tag_lbl = QtWidgets.QLabel("<b>{}</b>:".format(tag))
                tag_lbl.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                combo = QtWidgets.QComboBox()
                combo.setMinimumWidth(200)
                for i, name in enumerate(self._names):
                    if tag in self._tags_map.get(name, []):
                        title = self._titles[i]
                        if self._titles.count(title) > 1:
                            combo.addItem("{} ({})".format(title, name), name)
                        else:
                            combo.addItem(title, name)
                ok_btn = QtWidgets.QPushButton("OK")
                ok_btn.setMaximumWidth(40)
                ok_btn.clicked.connect(partial(self._okPressed, combo))
                ok_btn.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
                ok_btn.customContextMenuRequested.connect(
                    partial(self._okRightClicked, combo))

                grid.addWidget(tag_lbl, row, 0)
                grid.addWidget(combo, row, 1)
                grid.addWidget(ok_btn, row, 2)
                row += 1

        # By backdrop
        if self._all_bds:
            lbl = QtWidgets.QLabel("<i>Backdrops</i>")
            lbl.setStyleSheet("color:#666; padding-left:3px;")
            grid.addWidget(lbl, row, 0, 1, 3)
            row += 1

            for bd in self._all_bds:
                bd_lbl = QtWidgets.QLabel("{}:".format(bd))
                bd_lbl.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                combo = QtWidgets.QComboBox()
                combo.setMinimumWidth(200)
                for i, name in enumerate(self._names):
                    if bd in self._bd_map.get(name, []):
                        title = self._titles[i]
                        combo.addItem(title, name)
                ok_btn = QtWidgets.QPushButton("OK")
                ok_btn.setMaximumWidth(40)
                ok_btn.clicked.connect(partial(self._okPressed, combo))
                ok_btn.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
                ok_btn.customContextMenuRequested.connect(
                    partial(self._okRightClicked, combo))
                grid.addWidget(bd_lbl, row, 0)
                grid.addWidget(combo, row, 1)
                grid.addWidget(ok_btn, row, 2)
                row += 1

        layout.addWidget(scroll)

        # "All" dropdown at bottom
        bottom = QtWidgets.QHBoxLayout()
        all_lbl = QtWidgets.QLabel("<b>all</b>:")
        all_lbl.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self._all_combo = QtWidgets.QComboBox()
        self._all_combo.setMinimumWidth(200)
        sorted_pairs = sorted(zip(self._titles, self._names),
                              key=lambda p: p[0].lower())
        for title, name in sorted_pairs:
            if self._titles.count(title) > 1:
                self._all_combo.addItem("{} ({})".format(title, name), name)
            else:
                self._all_combo.addItem(title, name)
        all_ok = QtWidgets.QPushButton("OK")
        all_ok.setMaximumWidth(40)
        all_ok.clicked.connect(partial(self._okPressed, self._all_combo))
        all_ok.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        all_ok.customContextMenuRequested.connect(
            partial(self._okRightClicked, self._all_combo))
        bottom.addWidget(all_lbl)
        bottom.addWidget(self._all_combo)
        bottom.addWidget(all_ok)
        layout.addLayout(bottom)

        # Cancel
        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        layout.addWidget(cancel_btn)

        self.setLayout(layout)
        self.resize(400, 300)

    def _okPressed(self, combo):
        name = combo.currentData()
        if name and nuke.exists(name):
            self.chosen_anchors = [nuke.toNode(name)]
            self.accept()

    def _okRightClicked(self, combo):
        name = combo.currentData()
        if name and nuke.exists(name):
            self.chosen_anchors.append(nuke.toNode(name))


class NewAnchorPanel(QtWidgets.QDialog):
    """Dialog for creating a new anchor with title and tags."""

    def __init__(self, title="New Stamp", default_title="",
                 existing_tags=None, default_tags=""):
        super(NewAnchorPanel, self).__init__()
        self.setWindowTitle(title)
        self.anchorTitle = default_title
        self.anchorTags = default_tags
        self._existing_tags = existing_tags or []
        self._initUI(default_title, default_tags)

    def _initUI(self, default_title, default_tags):
        layout = QtWidgets.QVBoxLayout()

        # Title
        layout.addWidget(QtWidgets.QLabel("Title:"))
        self._title_edit = QtWidgets.QLineEdit(default_title)
        self._title_edit.selectAll()
        layout.addWidget(self._title_edit)

        # Tags
        layout.addWidget(QtWidgets.QLabel("Tags (comma-separated):"))
        self._tags_edit = QtWidgets.QLineEdit(default_tags)
        layout.addWidget(self._tags_edit)

        # Buttons
        btns = QtWidgets.QHBoxLayout()
        ok_btn = QtWidgets.QPushButton("OK")
        ok_btn.clicked.connect(self._ok)
        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(ok_btn)
        btns.addWidget(cancel_btn)
        layout.addLayout(btns)

        self.setLayout(layout)
        self._title_edit.setFocus()

    def _ok(self):
        self.anchorTitle = self._title_edit.text()
        self.anchorTags = self._tags_edit.text()
        self.accept()


# =========================================================================
# MAIN ENTRY POINT (hotkey callback)
# =========================================================================

def goStamp(ns=None):
    """
    Main stamp function — bound to the hotkey (F8 by default).

    Behavior:
    - No selection + no anchors: create new anchor
    - No selection + anchors exist: show selector, create wired
    - Selected anchor: create wired from it
    - Selected wired: duplicate it
    - Selected other node: create anchor + wired from it
    """
    if ns is None:
        ns = nuke.selectedNodes()

    if not ns:
        if not totalAnchors():
            stampCreateAnchor(no_default_tag=True)
        else:
            stampCreateWired()
        return

    if len(ns) == 1 and ns[0].Class() in NodeExceptionClasses:
        if totalAnchors():
            stampCreateWired()
        return

    # Multiple nodes or single non-exception node
    if len(ns) > 10:
        if not nuke.ask("You have {} nodes selected.\n"
                        "Create stamps for all?".format(len(ns))):
            return

    extra_tags = []
    for n in ns:
        try:
            if n.Class() in NodeExceptionClasses:
                continue
            elif isAnchor(n):
                stampCreateWiredFromNode(n)
            elif isWired(n):
                stampDuplicateWired(n)
            else:
                extra_tags = stampCreateAnchor(n, extra_tags=extra_tags)
        except Exception as e:
            print("stamps_cpp: Error processing {}: {}".format(n.name(), e))
            continue


def stampCreateAnchor(node=None, extra_tags=None, no_default_tag=False):
    """Create a new anchor stamp, showing the title/tags dialog."""
    if extra_tags is None:
        extra_tags = []

    ns = nuke.selectedNodes()
    for n in ns:
        n.setSelected(False)

    if node is not None:
        node.setSelected(True)
        default_title = getDefaultTitle(realInput(node, stopOnLabel=True, mode="title"))
        default_tags = [nodeType(realInput(node, mode="tags"))]
        node_type = nodeType(realInput(node))

        # Dual-output nodes (e.g. ScanlineRender): let user pick output type
        if node_type == "2D" and canOutputDeep(node):
            p = nuke.Panel("Stamp Output Type")
            p.addEnumerationPulldown("Output:", "2D Deep")
            if not p.show():
                # User cancelled
                for n in ns:
                    n.setSelected(True)
                return extra_tags
            if p.value("Output:") == "Deep":
                node_type = "Deep"
                default_tags = ["Deep"]
                default_title = default_title + "_deep"

        window_title = "New Stamp: " + node.name()
    else:
        default_title = "Stamp"
        default_tags = []
        window_title = "New Stamp"

    orig_default_tags = list(default_tags)

    if no_default_tag:
        tags_str = ", ".join(extra_tags + [""])
    else:
        combined = list(dict.fromkeys(default_tags + extra_tags))
        combined = [t for t in combined if t]
        tags_str = ", ".join(combined + [""])

    panel = NewAnchorPanel(window_title, default_title, allTags(), tags_str)

    while True:
        if panel.exec_():
            title = panel.anchorTitle
            tags = panel.anchorTags
            if not titleIsLegal(title):
                nuke.message("Please set a valid title.")
                continue
            existing = findAnchorsByTitle(title)
            if existing:
                if not nuke.ask("There is already a Stamp titled '{}'. "
                                "Use this title anyway?".format(title)):
                    continue
            na = createAnchor(title=title, tags=tags,
                              input_node=node if node else None,
                              node_type=node_type if node else "2D")
            na.setYpos(na.ypos() + 20)
            stampCreateWiredFromNode(na)
            for n in ns:
                n.setSelected(True)
            if node:
                node.setSelected(False)
            extra_tags = [t.strip() for t in tags.split(",")
                          if t.strip() and t.strip() not in orig_default_tags]
            break
        else:
            break

    return extra_tags


def stampCreateWired(anchor=None):
    """Create a wired stamp, optionally showing selector if no anchor given."""
    if anchor is None:
        anchors = _selectAnchors()
        if not anchors:
            return None
        nws = []
        for i, a in enumerate(anchors):
            nt = "Deep" if a.Class() in (CLASS_DEEP_ANCHOR,) else "2D"
            nw = createWired(a, node_type=nt)
            nw.setInput(0, a)
            nws.append(nw)
            if i > 0:
                nws[i].setXYpos(nws[i-1].xpos() + 100, nws[i-1].ypos())
        return nws[-1] if nws else None
    else:
        return stampCreateWiredFromNode(anchor)


def stampCreateWiredFromNode(anchor):
    """Create a wired stamp from a specific anchor node."""
    return stampCreateWiredFromAnchor(anchor)


def stampDuplicateWired(wired):
    """Duplicate a wired stamp (copy with same anchor connection)."""
    a_name = wired["anchor"].value()
    if not nuke.exists(a_name):
        nuke.message("Anchor '{}' not found.".format(a_name))
        return None
    a = nuke.toNode(a_name)
    nt = "Deep" if wired.Class() in (CLASS_DEEP_WIRED,) else "2D"
    nw = createWired(a, node_type=nt)
    nw.setXYpos(wired.xpos() + 100, wired.ypos())
    return nw


def _selectAnchors():
    """Show the AnchorSelector dialog and return chosen anchors."""
    if not totalAnchors():
        nuke.message("No anchors found. Create some stamps first.")
        return None
    panel = AnchorSelector()
    if panel.exec_():
        return panel.chosen_anchors if panel.chosen_anchors else None
    return None


# =========================================================================
# LEGACY CONVERSION
# =========================================================================

def convertLegacyStamps():
    """
    Convert all Python-based Stamps (NoOp/DeepExpression with identifier knob)
    to native C++ StampAnchor/StampWired/StampDeepAnchor/StampDeepWired nodes.
    """
    converted = 0
    errors = 0

    # Classes to scan for legacy stamps
    legacy_classes = ["NoOp", "DeepExpression"]

    # Convert anchors
    for legacy_cls in legacy_classes:
        for n in list(nuke.allNodes(legacy_cls)):
            if not (n.knob("identifier") and n["identifier"].value() == "anchor"):
                continue
            try:
                title = n["title"].value() if n.knob("title") else ""
                tags = n["tags"].value() if n.knob("tags") else ""
                inp = n.input(0)
                x, y = n.xpos(), n.ypos()

                # Choose the right C++ class
                target_cls = CLASS_DEEP_ANCHOR if legacy_cls == "DeepExpression" else CLASS_ANCHOR

                na = nuke.createNode(target_cls, inpanel=False)
                na["title"].setValue(title)
                na["tags"].setValue(tags)
                na["tile_color"].setValue(0xFFFFFF01)
                na["note_font_size"].setValue(20)
                na["label"].setValue("[value title]")
                na["name"].setValue(n.name() + "_cpp")
                if inp:
                    na.setInput(0, inp)
                na.setXYpos(x, y)

                old_name = n.name()
                new_name = na.name()
                for w in nuke.allNodes():
                    if w.knob("anchor") and w["anchor"].value() == old_name:
                        w["anchor"].setValue(new_name)
                        w.setInput(0, na)

                nuke.delete(n)
                na["name"].setValue(old_name)
                converted += 1
            except Exception as e:
                errors += 1
                print("stamps_cpp: Error converting anchor {}: {}".format(n.name(), e))

    # Convert wireds
    for legacy_cls in legacy_classes:
        for n in list(nuke.allNodes(legacy_cls)):
            if not (n.knob("identifier") and n["identifier"].value() == "wired"):
                continue
            try:
                title = n["title"].value() if n.knob("title") else ""
                a_name = n["anchor"].value() if n.knob("anchor") else ""
                inp = n.input(0)
                x, y = n.xpos(), n.ypos()

                target_cls = CLASS_DEEP_WIRED if legacy_cls == "DeepExpression" else CLASS_WIRED

                nw = nuke.createNode(target_cls, inpanel=False)
                nw["title"].setValue(title)
                nw["anchor"].setValue(a_name)
                nw["tile_color"].setValue(0x01000001)
                nw["note_font_size"].setValue(20)
                nw["hide_input"].setValue(True)
                nw["label"].setValue("[value title]")
                if inp:
                    nw.setInput(0, inp)
                nw.setXYpos(x, y)

                old_name = n.name()
                for other in nuke.allNodes():
                    for i in range(other.inputs()):
                        if other.input(i) == n:
                            other.setInput(i, nw)

                nuke.delete(n)
                nw["name"].setValue(old_name)
                converted += 1
            except Exception as e:
                errors += 1
                print("stamps_cpp: Error converting wired {}: {}".format(n.name(), e))

    nuke.message("Converted {} stamps ({} errors).".format(converted, errors))


# =========================================================================
# REFRESH ALL STAMPS
# =========================================================================

def refreshStamps(ns=None):
    """Reconnect and refresh all stamps."""
    if ns is None:
        ns = allWireds()
    for w in ns:
        try:
            a_name = w["anchor"].value()
            if nuke.exists(a_name):
                a = nuke.toNode(a_name)
                w.setInput(0, a)
                _setWiredStyle(w, 0)
                _updateWiredTagsDisplay(w)
            else:
                _setWiredStyle(w, 1)
        except:
            pass


# =========================================================================
# HELP
# =========================================================================

def showHelp():
    nuke.message(STAMPS_HELP)


# =========================================================================
# CREATION CALLBACKS  (auto-reconnect on paste / script load)
# =========================================================================
#
# Why this is needed at all:
#   - C++ toReconnect Bool_knob is serialized to the script. After the
#     first reconnect on a fresh node it's stored as 0, so on paste the
#     loaded value overrides the constructor's `true` default and the
#     C++ knob_changed reconnect path never fires.
#   - Op::knob_changed isn't reliably called on paste anyway — it's tied
#     to user interaction / panel show.
#
# Strategy:
#   - Register addOnCreate WITHOUT nodeClass filter and gate inside —
#     more compatible across Nuke versions and NDK class registration
#     timing.
#   - Defer the actual reconnect by one main-thread tick. Nuke applies
#     pasted input connections AFTER addOnCreate fires; if we setInput
#     too early it can be overwritten. Deferring also covers the
#     script-load case where the anchor node may be created later in
#     script order than the wired.
#   - Diagnostics print to the Script Editor so misbehaviour is visible.

# Set to False once you've verified it's working to silence the prints.
STAMPS_DEBUG_ONCREATE = False


def _stampDeferredReconnect(node_name):
    """Run on the next event-loop tick, after Nuke finishes paste/load."""
    n = nuke.toNode(node_name)
    if n is None:
        if STAMPS_DEBUG_ONCREATE:
            nuke.tprint("[Stamps C++] deferred: node %s vanished" % node_name)
        return

    a_name = n["anchor"].value() if n.knob("anchor") else ""
    cur = n.input(0)
    cur_name = cur.name() if cur else "None"

    if STAMPS_DEBUG_ONCREATE:
        nuke.tprint("[Stamps C++] deferred reconnect: %s "
                    "(anchor knob=%r, current input=%s)"
                    % (n.name(), a_name, cur_name))

    if not a_name:
        if STAMPS_DEBUG_ONCREATE:
            nuke.tprint("[Stamps C++]   anchor knob empty — nothing to do")
        return

    if not nuke.exists(a_name):
        if STAMPS_DEBUG_ONCREATE:
            nuke.tprint("[Stamps C++]   anchor %s does not exist in script"
                        % a_name)
        try:
            _setWiredStyle(n, 1)
        except Exception:
            pass
        return

    a = nuke.toNode(a_name)
    try:
        n.setInput(0, a)
        _setWiredStyle(n, 0)
        if STAMPS_DEBUG_ONCREATE:
            nuke.tprint("[Stamps C++]   connected %s -> %s"
                        % (n.name(), a_name))
    except Exception as e:
        nuke.tprint("[Stamps C++]   setInput failed: %s" % e)


# Lazy-imported and cached Qt timer factory — used to truly defer the
# reconnect until AFTER the paste / script-load tcl call stack unwinds.
# nuke.executeInMainThread runs inline when called from the main thread,
# which is no defer at all and lets paste's stack-based input wiring
# clobber our setInput. QTimer.singleShot(0) queues onto the next event
# loop tick, which is the earliest moment paste is guaranteed finished.
_STAMPS_QTIMER = None

def _stampsQtTimer():
    global _STAMPS_QTIMER
    if _STAMPS_QTIMER is not None:
        return _STAMPS_QTIMER
    try:
        from PySide2 import QtCore  # Nuke 13/14/15/16
    except ImportError:
        try:
            from PySide6 import QtCore  # Nuke 17+
        except ImportError:
            return None
    _STAMPS_QTIMER = QtCore.QTimer
    return _STAMPS_QTIMER


def _stampOnCreate():
    """Fires for every node creation; gates on isWired() internally so it
    catches all three wired flavours: StampWired (C++ 2D), StampDeepWired
    (C++ Deep), and NoOp with identifier='wired' (3D / Camera / Axis /
    Particle, plus legacy Python Stamps)."""
    n = nuke.thisNode()
    if n is None:
        return
    if not isWired(n):
        return

    if STAMPS_DEBUG_ONCREATE:
        nuke.tprint("[Stamps C++] onCreate: %s (class=%s)"
                    % (n.name(), n.Class()))

    name = n.name()

    QTimer = _stampsQtTimer()
    if QTimer is not None:
        # Real defer: next Qt event-loop tick, after the current paste /
        # tcl call stack finishes. 0 ms is enough — the queue itself is
        # what's important, not the delay.
        QTimer.singleShot(0, lambda nm=name: _stampDeferredReconnect(nm))
    else:
        # Fallback: best-effort inline. May still be clobbered by paste,
        # but better than nothing if Qt is unavailable (terminal mode).
        _stampDeferredReconnect(name)


def _registerStampCallbacks():
    """Register addOnCreate (idempotent — survives module reloads)."""
    # Best-effort removal of any previous registration
    for kw in (dict(), dict(nodeClass="StampWired"),
               dict(nodeClass="StampDeepWired")):
        try:
            nuke.removeOnCreate(_stampOnCreate, **kw)
        except Exception:
            pass
    # Register WITHOUT nodeClass — gate inside the callback
    nuke.addOnCreate(_stampOnCreate)
    if STAMPS_DEBUG_ONCREATE:
        nuke.tprint("[Stamps C++] addOnCreate registered (unfiltered)")


# =========================================================================
# MENU SETUP
# =========================================================================

def stampBuildMenus():
    """Register menus and shortcuts."""
    toolbar = nuke.menu("Nodes")
    stamps_menu = toolbar.addMenu("Stamps", icon="Stamps.png")
    stamps_menu.addCommand("Stamp (hotkey)", "stamps_cpp.goStamp()",
                           STAMPS_SHORTCUT)
    stamps_menu.addCommand("---", "", "")
    stamps_menu.addCommand("Refresh All Stamps", "stamps_cpp.refreshStamps()")
    stamps_menu.addCommand("Convert Legacy Stamps", "stamps_cpp.convertLegacyStamps()")
    stamps_menu.addCommand("---", "", "")
    stamps_menu.addCommand("Help", "stamps_cpp.showHelp()")


# Always register creation callbacks (also useful in -t / batch mode for
# script load reconnection). Menus only in GUI mode.
_registerStampCallbacks()

if nuke.GUI:
    stampBuildMenus()