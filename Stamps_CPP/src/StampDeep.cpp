// StampDeep.cpp - Deep data stamp nodes for Stamps C++ NDK plugin
//
// DeepFilterOp-based pass-through nodes for Deep compositing data.
// Same anchor/wired pattern as the 2D Iop versions, but inherits from
// DeepFilterOp so Deep samples pass through correctly.
//
// The original Python Stamps used DeepExpression nodes for Deep stamps.
// This C++ version is a proper DeepFilterOp with zero expression overhead.
//
// Original concept: Adrian Pueyo and Alexey Kuchinski (BSD-2-Clause)
// C++ port by Peter Mercell

#include "StampCommon.h"

#include "DDImage/DeepFilterOp.h"
#include "DDImage/Knobs.h"
#include "DDImage/Thread.h"

using namespace DD::Image;

namespace stamps {

// ===================================================================
// StampDeepAnchor
// ===================================================================

class StampDeepAnchor : public DeepFilterOp {
public:
    StampDeepAnchor(Node* node) : DeepFilterOp(node),
        _identifier("anchor"),
        _showing(0)
    {
    }

    ~StampDeepAnchor() override {}

    const char* Class() const override { return CLASS_DEEP_ANCHOR; }
    const char* node_help() const override { return HELP_STRING; }
    static const Op::Description desc;

    int minimum_inputs()  const override { return 0; }
    int maximum_inputs()  const override { return 1; }

    Op* default_input(int) const override { return nullptr; }

    // -----------------------------------------------------------
    // Deep pass-through
    // -----------------------------------------------------------
    void _validate(bool for_real) override {
        DeepFilterOp::_validate(for_real);
    }

    bool doDeepEngine(Box box, const ChannelSet& channels,
                      DeepOutputPlane& plane) override {
        if (!input0()) return true;

        DeepPlane inPlane;
        if (!input0()->deepEngine(box, channels, inPlane))
            return false;

        plane = DeepOutputPlane(channels, box);
        const int nChans = channels.size();
        for (Box::iterator it = box.begin(); it != box.end(); ++it) {
            DeepPixel pixel = inPlane.getPixel(it);
            const int samples = pixel.getSampleCount();

            if (samples == 0) {
                plane.addHole();
                continue;
            }

            // All samples for this pixel in one DeepOutPixel
            DeepOutPixel outPx(nChans * samples);
            for (int s = 0; s < samples; ++s) {
                const float* sampleData = pixel.getUnorderedSample(s);
                for (int c = 0; c < nChans; ++c) {
                    outPx.push_back(sampleData[c]);
                }
            }
            plane.addPixel(outPx);
        }
        return true;
    }

    // -----------------------------------------------------------
    // Knobs — same pattern as StampAnchor (2D)
    // -----------------------------------------------------------
    void knobs(Knob_Callback f) override {
        Tab_knob(f, 0, "Deep Anchor Stamp");

        String_knob(f, &_identifier, "identifier", "identifier");
        SetFlags(f, Knob::INVISIBLE);

        String_knob(f, &_title, "title", "Title:");
        Tooltip(f, TITLE_TOOLTIP);

        String_knob(f, &_prev_title, "prev_title", "");
        SetFlags(f, Knob::INVISIBLE | Knob::DO_NOT_WRITE);

        String_knob(f, &_prev_name, "prev_name", "");
        SetFlags(f, Knob::INVISIBLE | Knob::DO_NOT_WRITE);

        Int_knob(f, &_showing, "showing", "");
        SetFlags(f, Knob::INVISIBLE | Knob::DO_NOT_WRITE);

        String_knob(f, &_tags, "tags", "Tags");
        Tooltip(f, TAGS_TOOLTIP);

        Divider(f, "");

        Text_knob(f, "Stamps:");
        SetFlags(f, Knob::STARTLINE);

        PyScript_knob(f,
            "import stamps_cpp; stamps_cpp.stampCreateWiredFromAnchor(nuke.thisNode())",
            "createStamp", "new");
        ClearFlags(f, Knob::STARTLINE);

        PyScript_knob(f,
            "import stamps_cpp; stamps_cpp.anchorSelectWireds(nuke.thisNode())",
            "selectStamps", "select");
        ClearFlags(f, Knob::STARTLINE);

        PyScript_knob(f,
            "import stamps_cpp; stamps_cpp.anchorReconnectWireds(nuke.thisNode())",
            "reconnectStamps", "reconnect");
        ClearFlags(f, Knob::STARTLINE);

        PyScript_knob(f,
            "import stamps_cpp; stamps_cpp.wiredZoomNext(nuke.thisNode().name())",
            "zoomNext", "zoom next");
        ClearFlags(f, Knob::STARTLINE);

        Divider(f, "");

        PyScript_knob(f, "import stamps_cpp; stamps_cpp.showHelp()",
                      "buttonHelp", "Help");
        Text_knob(f, " ", STAMPS_VERSION);
        ClearFlags(f, Knob::STARTLINE);
    }

    int knob_changed(Knob* k) override {
        const std::string& kn = k->name();
        if (kn == "xpos" || kn == "ypos") return 0;

        if (kn == "title") {
            _prev_title = _title;
            script_command("__import__('stamps_cpp')._anchorTitleChanged()");
            script_unlock();
            return 1;
        }
        if (kn == "name") {
            script_command("__import__('stamps_cpp')._anchorNameChanged()");
            script_unlock();
            _prev_name = Op::node_name();
            return 1;
        }
        if (kn == "tags") {
            script_command("__import__('stamps_cpp')._anchorTagsChanged()");
            script_unlock();
            return 1;
        }
        return DeepFilterOp::knob_changed(k);
    }

private:
    std::string _identifier;
    std::string _title;
    std::string _prev_title;
    std::string _prev_name;
    std::string _tags;
    int  _showing;
};


// ===================================================================
// StampDeepWired
// ===================================================================

class StampDeepWired : public DeepFilterOp {
public:
    StampDeepWired(Node* node) : DeepFilterOp(node),
        _identifier("wired"),
        _to_reconnect(true),
        _lock_callbacks(false)
    {
    }

    ~StampDeepWired() override {}

    const char* Class() const override { return CLASS_DEEP_WIRED; }
    const char* node_help() const override { return HELP_STRING; }
    static const Op::Description desc;

    int minimum_inputs()  const override { return 0; }
    int maximum_inputs()  const override { return 1; }

    Op* default_input(int) const override { return nullptr; }

    // -----------------------------------------------------------
    // Deep pass-through
    // -----------------------------------------------------------
    void _validate(bool for_real) override {
        DeepFilterOp::_validate(for_real);
    }

    bool doDeepEngine(Box box, const ChannelSet& channels,
                      DeepOutputPlane& plane) override {
        if (!input0()) return true;

        DeepPlane inPlane;
        if (!input0()->deepEngine(box, channels, inPlane))
            return false;

        plane = DeepOutputPlane(channels, box);
        const int nChans = channels.size();
        for (Box::iterator it = box.begin(); it != box.end(); ++it) {
            DeepPixel pixel = inPlane.getPixel(it);
            const int samples = pixel.getSampleCount();

            if (samples == 0) {
                plane.addHole();
                continue;
            }

            DeepOutPixel outPx(nChans * samples);
            for (int s = 0; s < samples; ++s) {
                const float* sampleData = pixel.getUnorderedSample(s);
                for (int c = 0; c < nChans; ++c) {
                    outPx.push_back(sampleData[c]);
                }
            }
            plane.addPixel(outPx);
        }
        return true;
    }

    // -----------------------------------------------------------
    // Knobs — same pattern as StampWired (2D)
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

        // Tags / backdrops display (read-only)
        String_knob(f, &_tags_display, "tags", "Tags:");
        SetFlags(f, Knob::DISABLED);

        String_knob(f, &_backdrops_display, "backdrops", "Backdrops:");
        SetFlags(f, Knob::DISABLED);

        Divider(f, "");

        // Anchor name reference (stored, needed for reconnection)
        String_knob(f, &_anchor_name, "anchor", "Anchor:");

        // --- Anchor section ---
        Text_knob(f, "Anchor:", "");

        PyScript_knob(f,
            "import stamps_cpp; stamps_cpp.wiredShowAnchor(nuke.thisNode())",
            "show_anchor", " show anchor ");
        ClearFlags(f, Knob::STARTLINE);

        PyScript_knob(f,
            "import stamps_cpp; stamps_cpp.wiredZoomAnchor(nuke.thisNode())",
            "zoom_anchor", "zoom anchor");
        ClearFlags(f, Knob::STARTLINE);

        // --- Stamps section ---
        Text_knob(f, "Stamps:", "");

        PyScript_knob(f,
            "import stamps_cpp; stamps_cpp.wiredZoomNext()",
            "zoomNext", " zoom next ");
        ClearFlags(f, Knob::STARTLINE);

        PyScript_knob(f,
            "import stamps_cpp; stamps_cpp.wiredSelectSimilar()",
            "selectSimilar", " select similar ");
        ClearFlags(f, Knob::STARTLINE);

        Newline(f);

        // --- Reconnect section ---
        Text_knob(f, "Reconnect:", "");

        PyScript_knob(f,
            "import stamps_cpp; stamps_cpp.wiredReconnect(nuke.thisNode())",
            "reconnect_this", "this");
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

    int knob_changed(Knob* k) override {
        const std::string& kn = k->name();
        if (kn == "xpos" || kn == "ypos") return 0;
        if (_lock_callbacks) return 0;

        if (_to_reconnect) {
            _to_reconnect = false;
            Knob* tr = knob("toReconnect");
            if (tr) tr->set_value(0);
            script_command("__import__('stamps_cpp')._wiredDoReconnect()");
            script_unlock();
            return 1;
        }

        if (kn == "inputChange") {
            Knob* nfs = knob("note_font_size");
            Knob* nfc = knob("note_font_color");
            if (nfs && nfc) {
                if (input(0)) {
                    nfs->set_value(DEFAULT_FONT_SIZE);
                    nfc->set_value(0);
                } else {
                    nfs->set_value(DEFAULT_FONT_SIZE * 2);
                    nfc->set_value(BROKEN_FONT_COLOR);
                }
            }
            return 1;
        }

        if (kn == "title") {
            _prev_title = _title;
            script_command("__import__('stamps_cpp')._wiredTitleChanged()");
            script_unlock();
            return 1;
        }

        if (kn == "showPanel") {
            script_command("__import__('stamps_cpp')._wiredUpdateTagsDisplay()");
            script_unlock();
            return 1;
        }

        return DeepFilterOp::knob_changed(k);
    }

private:
    std::string _identifier;
    std::string _title;
    std::string _prev_title;
    std::string _anchor_name;
    std::string _tags_display;
    std::string _backdrops_display;
    bool _to_reconnect;
    bool _lock_callbacks;
};


// ===================================================================
// Registration — use 2-arg Description (menu path via menu.py)
// ===================================================================

static Op* buildDeepAnchor(Node* node) { return new StampDeepAnchor(node); }
static Op* buildDeepWired(Node* node)  { return new StampDeepWired(node); }

const Op::Description StampDeepAnchor::desc(
    CLASS_DEEP_ANCHOR,
    buildDeepAnchor
);

const Op::Description StampDeepWired::desc(
    CLASS_DEEP_WIRED,
    buildDeepWired
);

} // namespace stamps
