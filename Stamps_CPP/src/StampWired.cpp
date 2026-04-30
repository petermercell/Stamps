// StampWired.cpp - Wired stamp node for Stamps C++ NDK plugin
//
// A zero-cost pass-through Iop with a hidden input connection to its
// parent StampAnchor. Handles auto-reconnection on copy-paste via the
// toReconnect flag and knob_changed callback.
//
// Performance advantage: knob_changed runs in C++ without GIL acquisition.
// Only operations requiring nuke.toNode() or nuke.ask() are dispatched
// to Python via script_command().
//
// Original concept: Adrian Pueyo and Alexey Kuchinski (BSD-2-Clause)
// C++ port by Peter Mercell

#include "StampCommon.h"

#include "DDImage/Iop.h"
#include "DDImage/NukeWrapper.h"
#include "DDImage/Row.h"
#include "DDImage/Knobs.h"
#include "DDImage/Thread.h"

using namespace DD::Image;

namespace stamps {

class StampWired : public Iop {
public:
    StampWired(Node* node) : Iop(node),
        _identifier("wired"),
        _to_reconnect(true),
        _lock_callbacks(false)
    {
    }

    ~StampWired() override {}

    const char* Class() const override { return CLASS_WIRED; }
    const char* node_help() const override { return HELP_STRING; }
    static const Iop::Description desc;

    int minimum_inputs()  const override { return 0; }
    int maximum_inputs()  const override { return 1; }

    bool test_input(int index, Op* op) const override {
        return (index == 0) ? dynamic_cast<Iop*>(op) != nullptr : false;
    }

    void append(Hash& hash) override {}

    void _validate(bool for_real) override {
        if (input(0)) {
            input(0)->validate(for_real);
            copy_info();
            set_out_channels(Mask_All);
        } else {
            info_.black_outside(true);
            set_out_channels(Mask_None);
        }
    }

    void _request(int x, int y, int r, int t, ChannelMask m, int count) override {
        if (input(0))
            input(0)->request(x, y, r, t, m, count);
    }

    void _open()  override { if (input(0)) input(0)->open(); }
    void _close() override { if (input(0)) input(0)->close(); }

    void engine(int y, int x, int r, ChannelMask channels, Row& row) override {
        if (input(0)) {
            input(0)->get(y, x, r, channels, row);
        } else {
            row.erase(channels);
        }
    }

    // -----------------------------------------------------------
    // Knobs
    // -----------------------------------------------------------
    void knobs(Knob_Callback f) override {

        // Hidden metadata knobs
        String_knob(f, &_identifier, "identifier", "identifier");
        SetFlags(f, Knob::INVISIBLE);

        Bool_knob(f, &_lock_callbacks, "lockCallbacks", "");
        SetFlags(f, Knob::INVISIBLE | Knob::DO_NOT_WRITE);

        Bool_knob(f, &_to_reconnect, "toReconnect", "");
        SetFlags(f, Knob::INVISIBLE);

        // Title
        String_knob(f, &_title, "title", "Title:");
        Tooltip(f, TITLE_TOOLTIP);

        String_knob(f, &_prev_title, "prev_title", "");
        SetFlags(f, Knob::INVISIBLE | Knob::DO_NOT_WRITE);

        // Tags / backdrops display (read-only, shows anchor's values)
        String_knob(f, &_tags_display, "tags", "Tags:");
        SetFlags(f, Knob::DISABLED);
        Tooltip(f, "Tags of this stamp's Anchor. Click 'show anchor' to change them.");

        String_knob(f, &_backdrops_display, "backdrops", "Backdrops:");
        SetFlags(f, Knob::DISABLED);
        Tooltip(f, "Labels of backdrop nodes that contain this stamp's Anchor.");

        Divider(f, "");

        // Anchor name reference (stored, needed for reconnection)
        String_knob(f, &_anchor_name, "anchor", "Anchor:");

        // --- Anchor section ---
        // 3-arg Text_knob puts label in the right-aligned label column
        Text_knob(f, "Anchor:", "");

        PyScript_knob(f,
            "import stamps_cpp; stamps_cpp.wiredShowAnchor(nuke.thisNode())",
            "show_anchor", " show anchor ");
        Tooltip(f, "Show the properties panel for this Stamp's Anchor.");
        ClearFlags(f, Knob::STARTLINE);

        PyScript_knob(f,
            "import stamps_cpp; stamps_cpp.wiredZoomAnchor(nuke.thisNode())",
            "zoom_anchor", "zoom anchor");
        Tooltip(f, "Navigate to this Stamp's Anchor on the Node Graph.");
        ClearFlags(f, Knob::STARTLINE);

        // --- Stamps section ---
        Text_knob(f, "Stamps:", "");

        PyScript_knob(f,
            "import stamps_cpp; stamps_cpp.wiredZoomNext()",
            "zoomNext", " zoom next ");
        Tooltip(f, "Navigate to this Stamp's next sibling.");
        ClearFlags(f, Knob::STARTLINE);

        PyScript_knob(f,
            "import stamps_cpp; stamps_cpp.wiredSelectSimilar()",
            "selectSimilar", " select similar ");
        Tooltip(f, "Select all similar Stamps.");
        ClearFlags(f, Knob::STARTLINE);

        Newline(f);

        // --- Reconnect section ---
        Text_knob(f, "Reconnect:", "");

        PyScript_knob(f,
            "import stamps_cpp; stamps_cpp.wiredReconnect(nuke.thisNode())",
            "reconnect_this", "this");
        Tooltip(f, "Reconnect this Stamp to its Anchor.");
        ClearFlags(f, Knob::STARTLINE);

        PyScript_knob(f,
            "import stamps_cpp; stamps_cpp.wiredReconnectSimilar()",
            "reconnect_similar", "similar");
        ClearFlags(f, Knob::STARTLINE);

        PyScript_knob(f,
            "import stamps_cpp; stamps_cpp.wiredReconnectAll()",
            "reconnect_all", "all");
        ClearFlags(f, Knob::STARTLINE);

        Newline(f);

        // --- Advanced Reconnection ---
        Divider(f, "Advanced Reconnection");

        Text_knob(f, "<font color=gold>By Title:", "");

        PyScript_knob(f,
            "import stamps_cpp; stamps_cpp.wiredReconnectByTitle()",
            "reconnect_by_title_this", "this");
        ClearFlags(f, Knob::STARTLINE);

        PyScript_knob(f,
            "import stamps_cpp; stamps_cpp.wiredReconnectByTitleSimilar()",
            "reconnect_by_title_similar", "similar");
        ClearFlags(f, Knob::STARTLINE);

        PyScript_knob(f,
            "import stamps_cpp; stamps_cpp.wiredReconnectByTitleSelected()",
            "reconnect_by_title_selected", "selected");
        ClearFlags(f, Knob::STARTLINE);

        Text_knob(f, "<font color=orangered>By Selection:", "");

        PyScript_knob(f,
            "import stamps_cpp; stamps_cpp.wiredReconnectBySelection()",
            "reconnect_by_selection_this", "this");
        ClearFlags(f, Knob::STARTLINE);

        PyScript_knob(f,
            "import stamps_cpp; stamps_cpp.wiredReconnectBySelectionSimilar()",
            "reconnect_by_selection_similar", "similar");
        ClearFlags(f, Knob::STARTLINE);

        PyScript_knob(f,
            "import stamps_cpp; stamps_cpp.wiredReconnectBySelectionSelected()",
            "reconnect_by_selection_selected", "selected");
        ClearFlags(f, Knob::STARTLINE);

    }

    // -----------------------------------------------------------
    // knob_changed
    // -----------------------------------------------------------
    int knob_changed(Knob* k) override {
        const std::string& kn = k->name();

        // Fast path: ignore position changes
        if (kn == "xpos" || kn == "ypos")
            return 0;

        // Ignore selection-based reconnect buttons (handled by PyScript_knob)
        if (kn == "reconnect_by_selection_this" ||
            kn == "reconnect_by_selection_similar")
            return 0;

        if (_lock_callbacks)
            return 0;

        // --- Reconnection on paste/create ---
        if (_to_reconnect) {
            _to_reconnect = false;
            Knob* tr = knob("toReconnect");
            if (tr) tr->set_value(0);
            script_command("__import__('stamps_cpp')._wiredDoReconnect()");
            script_unlock();
            return 1;
        }

        // --- Input changed ---
        if (kn == "inputChange") {
            _updateStyleFromInput();
            return 1;
        }

        // --- Title changed ---
        if (kn == "title") {
            _prev_title = _title;
            script_command("__import__('stamps_cpp')._wiredTitleChanged()");
            script_unlock();
            return 1;
        }

        // --- Panel opened ---
        if (kn == "showPanel") {
            script_command("__import__('stamps_cpp')._wiredUpdateTagsDisplay()");
            script_unlock();
            return 1;
        }

        // --- Selected (first activation after paste) ---
        if (kn == "selected")
            return 0;

        return Iop::knob_changed(k);
    }

    const char* input_label(int, char*) const override { return nullptr; }

private:
    void _updateStyleFromInput() {
        Knob* nfs = knob("note_font_size");
        Knob* nfc = knob("note_font_color");
        if (!nfs || !nfc) return;

        Op* inp = input(0);
        if (!inp) {
            nfs->set_value(DEFAULT_FONT_SIZE * 2);
            nfc->set_value(BROKEN_FONT_COLOR);
            return;
        }

        const char* inp_class = inp->Class();
        bool is_anchor = streq(inp_class, CLASS_ANCHOR);

        if (!is_anchor && streq(inp_class, "NoOp")) {
            Knob* id_knob = inp->knob("identifier");
            if (id_knob) {
                is_anchor = true;
            }
        }

        if (is_anchor) {
            nfs->set_value(DEFAULT_FONT_SIZE);
            nfc->set_value(0);
        } else {
            nfs->set_value(DEFAULT_FONT_SIZE * 2);
            nfc->set_value(BROKEN_FONT_COLOR);
        }
    }

    // Knob storage — Nuke 17 String_knob requires std::string*
    std::string _identifier;
    std::string _title;
    std::string _prev_title;
    std::string _anchor_name;
    std::string _tags_display;
    std::string _backdrops_display;
    bool _to_reconnect;
    bool _lock_callbacks;
};

// Registration — use 2-arg Description (menu path via menu.py)
static Iop* buildWired(Node* node) { return new StampWired(node); }

const Iop::Description StampWired::desc(
    CLASS_WIRED,
    buildWired
);

} // namespace stamps
