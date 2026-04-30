// StampCommon.h - Shared constants and utilities for Stamps C++ NDK plugin
//
// Original concept: Adrian Pueyo and Alexey Kuchinski (BSD-2-Clause)
// C++ port by Peter Mercell

#pragma once

#include "DDImage/Op.h"
#include "DDImage/Knobs.h"

#include <string>
#include <vector>
#include <cstring>

namespace stamps {

// ---------------------------------------------------------------
// Version / class names
// ---------------------------------------------------------------
static const char* STAMPS_VERSION = "v2.0-cpp";

// Op class names — must match Description registration
static const char* CLASS_ANCHOR      = "StampAnchor";
static const char* CLASS_WIRED       = "StampWired";
static const char* CLASS_DEEP_ANCHOR = "StampDeepAnchor";
static const char* CLASS_DEEP_WIRED  = "StampDeepWired";

// Default tile colors (0xRRGGBBAA)
static const unsigned int ANCHOR_TILE_COLOR = 0xFFFFFF01;
static const unsigned int WIRED_TILE_COLOR  = 0x01000001;
static const unsigned int BROKEN_FONT_COLOR = 0xFF0000FF;

static const int DEFAULT_FONT_SIZE = 20;

// Tooltips
static const char* TITLE_TOOLTIP =
    "Displayed name on the Node Graph for this Stamp and its Anchor.\n"
    "IMPORTANT: This is only for display purposes, and is different "
    "from the internal node name.";

static const char* TAGS_TOOLTIP =
    "Comma-separated tags to help find this Anchor via the Stamp Selector.";

static const char* HELP_STRING =
    "Stamps C++ by Peter Mercell.\n"
    "Based on Stamps by Adrian Pueyo and Alexey Kuchinski.\n"
    "Smart node connection system for Nuke.\n"
    "Native C++ pass-through with zero pixel-processing overhead.";

// ---------------------------------------------------------------
// Inline helpers
// ---------------------------------------------------------------

// Nuke 17: Knob::name() returns const std::string&, so provide
// overloads that accept both const char* and const std::string&.

inline bool streq(const char* a, const char* b) {
    if (!a || !b) return false;
    return std::strcmp(a, b) == 0;
}

inline bool streq(const std::string& a, const char* b) {
    if (!b) return false;
    return a == b;
}

inline bool streq(const char* a, const std::string& b) {
    if (!a) return false;
    return b == a;
}

// Safe strncpy that always null-terminates
inline void safe_copy(char* dst, const char* src, size_t dst_size) {
    if (!src) { dst[0] = '\0'; return; }
    std::strncpy(dst, src, dst_size - 1);
    dst[dst_size - 1] = '\0';
}

} // namespace stamps
