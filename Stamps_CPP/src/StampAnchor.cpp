// StampAnchor.cpp - Anchor node for Stamps C++ NDK plugin
//
// A zero-cost pass-through Iop that acts as the "source" end of a stamp
// connection. All pixel data flows through unchanged via copy_info() / get().
//
// Nuke's GPU pipeline already handles pure-passthrough Iops optimally:
// copy_info() signals to the runtime that output == input, so pixel data
// stays on device without any explicit GPU kernel.
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

class StampAnchor : public Iop {
public:
    StampAnchor(Node* node) : Iop(node),
        _identifier("anchor"),
        _showing(0)
    {
    }

    ~StampAnchor() override {}

    const char* Class() const override { return CLASS_ANCHOR; }
    const char* node_help() const override { return HELP_STRING; }
    static const Iop::Description desc;

    int minimum_inputs()  const override { return 0; }
    int maximum_inputs()  const override { return 1; }

    // Input is optional — an anchor without a source is valid (will be black)
    bool test_input(int index, Op* op) const override {
        return (index == 0) ? dynamic_cast<Iop*>(op) != nullptr : false;
    }

    // Pure pass-through: hash equals input hash
    void append(Hash& hash) override {}

    void _validate(bool for_real) override {
        if (input(0)) {
            input(0)->validate(for_real);
            copy_info();            // bbox, format, channels from input
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

        // Nuke 17: Tab_knob no longer accepts (f, NAME, LABEL).
        // Use the 2-arg form: Tab_knob(f, LABEL).
        Tab_knob(f, 0, "Anchor Stamp");

        // Nuke 17: String_knob requires std::string* (not char[]).
        // Hidden identifier for legacy/cross-detection
        String_knob(f, &_identifier, "identifier", "identifier");
        SetFlags(f, Knob::INVISIBLE);

        // Title
        String_knob(f, &_title, "title", "Title:");
        Tooltip(f, TITLE_TOOLTIP);

        // Shadow copies for change detection
        String_knob(f, &_prev_title, "prev_title", "");
        SetFlags(f, Knob::INVISIBLE | Knob::DO_NOT_WRITE);

        String_knob(f, &_prev_name, "prev_name", "");
        SetFlags(f, Knob::INVISIBLE | Knob::DO_NOT_WRITE);

        Int_knob(f, &_showing, "showing", "");
        SetFlags(f, Knob::INVISIBLE | Knob::DO_NOT_WRITE);

        // Tags
        String_knob(f, &_tags, "tags", "Tags");
        Tooltip(f, TAGS_TOOLTIP);

        Divider(f, "");

        // Stamp management buttons (Python callbacks)
        // Nuke 17: Text_knob no longer accepts (f, NAME, LABEL, TEXT).
        // Use the 2-arg (f, TEXT) or 3-arg (f, LABEL, TEXT) form.
        Text_knob(f, "Stamps:");
        SetFlags(f, Knob::STARTLINE);

        // Each button imports stamps_cpp so it works even if not pre-imported
        PyScript_knob(f,
            "import stamps_cpp; stamps_cpp.stampCreateWiredFromAnchor(nuke.thisNode())",
            "createStamp", "new");
        Tooltip(f, "Create a new Stamp for this Anchor.");
        ClearFlags(f, Knob::STARTLINE);

        PyScript_knob(f,
            "import stamps_cpp; stamps_cpp.anchorSelectWireds(nuke.thisNode())",
            "selectStamps", "select");
        Tooltip(f, "Select all of this Anchor's Stamps.");
        ClearFlags(f, Knob::STARTLINE);

        PyScript_knob(f,
            "import stamps_cpp; stamps_cpp.anchorReconnectWireds(nuke.thisNode())",
            "reconnectStamps", "reconnect");
        Tooltip(f, "Reconnect all of this Anchor's Stamps.");
        ClearFlags(f, Knob::STARTLINE);

        PyScript_knob(f,
            "import stamps_cpp; stamps_cpp.wiredZoomNext(nuke.thisNode().name())",
            "zoomNext", "zoom next");
        Tooltip(f, "Navigate to this Anchor's next Stamp on the Node Graph.");
        ClearFlags(f, Knob::STARTLINE);

        Divider(f, "");

        PyScript_knob(f, "import stamps_cpp; stamps_cpp.showHelp()",
                      "buttonHelp", "Help");
        // Nuke 17: Text_knob 3-arg form: (f, LABEL, TEXT)
        Text_knob(f, " ", STAMPS_VERSION);
        ClearFlags(f, Knob::STARTLINE);

        Newline(f);
    }

    // -----------------------------------------------------------
    // knob_changed
    // -----------------------------------------------------------
    int knob_changed(Knob* k) override {
        // Nuke 17: Knob::name() returns const std::string&
        const std::string& kn = k->name();

        if (kn == "xpos" || kn == "ypos")
            return 0;

        if (kn == "title") {
            _prev_title = _title;
            script_command("__import__('stamps_cpp')._anchorTitleChanged()");
            script_unlock();
            return 1;
        }

        if (kn == "name") {
            script_command("__import__('stamps_cpp')._anchorNameChanged()");
            script_unlock();
            // Nuke 17: Op::node_name() returns std::string
            _prev_name = Op::node_name();
            return 1;
        }

        if (kn == "tags") {
            script_command("__import__('stamps_cpp')._anchorTagsChanged()");
            script_unlock();
            return 1;
        }

        if (kn == "showPanel")
            return 1;

        return Iop::knob_changed(k);
    }

    const char* input_label(int, char*) const override { return nullptr; }

private:
    std::string _identifier;
    std::string _title;
    std::string _prev_title;
    std::string _prev_name;
    std::string _tags;
    int  _showing;
};

// Registration — Nuke 17 deprecated the 3-arg Description(name, menu, constructor).
// Use the 2-arg form; menu placement is handled by menu.py.
static Iop* buildAnchor(Node* node) { return new StampAnchor(node); }

const Iop::Description StampAnchor::desc(
    CLASS_ANCHOR,            // Class() return value
    buildAnchor              // factory function
);

} // namespace stamps
