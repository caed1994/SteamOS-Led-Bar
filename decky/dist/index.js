const manifest = {"name":"SteamOS Utility Center"};
const API_VERSION = 2;
const internalAPIConnection = window.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit;
if (!internalAPIConnection) {
    throw new Error('[@decky/api]: Failed to connect to the loader as as the loader API was not initialized. This is likely a bug in Decky Loader.');
}
let api;
try {
    api = internalAPIConnection.connect(API_VERSION, manifest.name);
}
catch {
    api = internalAPIConnection.connect(1, manifest.name);
    console.warn(`[@decky/api] Requested API version ${API_VERSION} but the running loader only supports version 1. Some features may not work.`);
}
if (api._version != API_VERSION) {
    console.warn(`[@decky/api] Requested API version ${API_VERSION} but the running loader only supports version ${api._version}. Some features may not work.`);
}
const callable = api.callable;
const definePlugin = (fn) => {
    return (...args) => {
        return fn(...args);
    };
};

var DefaultContext = {
  color: undefined,
  size: undefined,
  className: undefined,
  style: undefined,
  attr: undefined
};
var IconContext = SP_REACT.createContext && /*#__PURE__*/SP_REACT.createContext(DefaultContext);

var _excluded = ["attr", "size", "title"];
function _objectWithoutProperties(e, t) { if (null == e) return {}; var o, r, i = _objectWithoutPropertiesLoose(e, t); if (Object.getOwnPropertySymbols) { var n = Object.getOwnPropertySymbols(e); for (r = 0; r < n.length; r++) o = n[r], -1 === t.indexOf(o) && {}.propertyIsEnumerable.call(e, o) && (i[o] = e[o]); } return i; }
function _objectWithoutPropertiesLoose(r, e) { if (null == r) return {}; var t = {}; for (var n in r) if ({}.hasOwnProperty.call(r, n)) { if (-1 !== e.indexOf(n)) continue; t[n] = r[n]; } return t; }
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
function ownKeys(e, r) { var t = Object.keys(e); if (Object.getOwnPropertySymbols) { var o = Object.getOwnPropertySymbols(e); r && (o = o.filter(function (r) { return Object.getOwnPropertyDescriptor(e, r).enumerable; })), t.push.apply(t, o); } return t; }
function _objectSpread(e) { for (var r = 1; r < arguments.length; r++) { var t = null != arguments[r] ? arguments[r] : {}; r % 2 ? ownKeys(Object(t), true).forEach(function (r) { _defineProperty(e, r, t[r]); }) : Object.getOwnPropertyDescriptors ? Object.defineProperties(e, Object.getOwnPropertyDescriptors(t)) : ownKeys(Object(t)).forEach(function (r) { Object.defineProperty(e, r, Object.getOwnPropertyDescriptor(t, r)); }); } return e; }
function _defineProperty(e, r, t) { return (r = _toPropertyKey(r)) in e ? Object.defineProperty(e, r, { value: t, enumerable: true, configurable: true, writable: true }) : e[r] = t, e; }
function _toPropertyKey(t) { var i = _toPrimitive(t, "string"); return "symbol" == typeof i ? i : i + ""; }
function _toPrimitive(t, r) { if ("object" != typeof t || !t) return t; var e = t[Symbol.toPrimitive]; if (void 0 !== e) { var i = e.call(t, r); if ("object" != typeof i) return i; throw new TypeError("@@toPrimitive must return a primitive value."); } return ("string" === r ? String : Number)(t); }
function Tree2Element(tree) {
  return tree && tree.map((node, i) => /*#__PURE__*/SP_REACT.createElement(node.tag, _objectSpread({
    key: i
  }, node.attr), Tree2Element(node.child)));
}
function GenIcon(data) {
  return props => /*#__PURE__*/SP_REACT.createElement(IconBase, _extends({
    attr: _objectSpread({}, data.attr)
  }, props), Tree2Element(data.child));
}
function IconBase(props) {
  var elem = conf => {
    var attr = props.attr,
      size = props.size,
      title = props.title,
      svgProps = _objectWithoutProperties(props, _excluded);
    var computedSize = size || conf.size || "1em";
    var className;
    if (conf.className) className = conf.className;
    if (props.className) className = (className ? className + " " : "") + props.className;
    return /*#__PURE__*/SP_REACT.createElement("svg", _extends({
      stroke: "currentColor",
      fill: "currentColor",
      strokeWidth: "0"
    }, conf.attr, attr, svgProps, {
      className: className,
      style: _objectSpread(_objectSpread({
        color: props.color || conf.color
      }, conf.style), props.style),
      height: computedSize,
      width: computedSize,
      xmlns: "http://www.w3.org/2000/svg"
    }), title && /*#__PURE__*/SP_REACT.createElement("title", null, title), props.children);
  };
  return IconContext !== undefined ? /*#__PURE__*/SP_REACT.createElement(IconContext.Consumer, null, conf => elem(conf)) : elem(DefaultContext);
}

// THIS FILE IS AUTO GENERATED
function FaLightbulb (props) {
  return GenIcon({"attr":{"viewBox":"0 0 352 512"},"child":[{"tag":"path","attr":{"d":"M96.06 454.35c.01 6.29 1.87 12.45 5.36 17.69l17.09 25.69a31.99 31.99 0 0 0 26.64 14.28h61.71a31.99 31.99 0 0 0 26.64-14.28l17.09-25.69a31.989 31.989 0 0 0 5.36-17.69l.04-38.35H96.01l.05 38.35zM0 176c0 44.37 16.45 84.85 43.56 115.78 16.52 18.85 42.36 58.23 52.21 91.45.04.26.07.52.11.78h160.24c.04-.26.07-.51.11-.78 9.85-33.22 35.69-72.6 52.21-91.45C335.55 260.85 352 220.37 352 176 352 78.61 272.91-.3 175.45 0 73.44.31 0 82.97 0 176zm176-80c-44.11 0-80 35.89-80 80 0 8.84-7.16 16-16 16s-16-7.16-16-16c0-61.76 50.24-112 112-112 8.84 0 16 7.16 16 16s-7.16 16-16 16z"},"child":[]}]})(props);
}

const getFullStatus = callable("get_full_status");
const getArea = callable("get_area");
const setArea = callable("set_area");
const doAction = callable("do_action");
// The scenes of the strip, in words. The command answers with the names that
// the configuration file uses.
const SCENE_WORDS = {
    steam: "Whatever Steam sets",
    off: "Off",
    color: "One colour",
    breath: "Breathing",
    patrol: "Patrol",
    rainbow: "Rainbow",
    fire: "Fire",
    aurora: "Aurora",
    temperature: "Temperature gauge",
    load: "Load gauge",
};
function words(value) {
    return SCENE_WORDS[value] ?? value;
}
function options(offered) {
    if (!Array.isArray(offered)) {
        return [];
    }
    return offered.map((one) => String(one)).map((one) => ({
        data: one,
        label: words(one),
    }));
}
function Choice(props) {
    // Field and Dropdown, and not DropdownItem.
    //
    // DropdownItem is Steam's own settings row with Steam's own dropdown inside
    // it, and this project cannot see what that row does with a prop it does
    // not know. renderButtonValue is declared by @decky/ui on Dropdown, so it
    // goes to Dropdown here and passes through nothing on the way.
    //
    // Field is the same row that DropdownItem draws, so the page looks as it
    // did.
    return (SP_JSX.jsx(DFL.Field, { label: props.label, description: props.description, childrenContainerWidth: "min", children: SP_JSX.jsx(DFL.Dropdown, { rgOptions: props.options, selectedOption: props.value, disabled: props.disabled, renderButtonValue: () => props.options.find((one) => one.data === props.value)?.label
                ?? props.value, onChange: (option) => props.onPick(String(option.data)) }) }));
}
// Three options and nothing behind them, for one question: does a dropdown
// follow the state of this page at all?
//
// Four changes to the real controls did nothing, and each of them was a guess
// about a component that cannot be run outside Game Mode. This answers the
// guess. If the line below the box changes and the box does not, the box is
// the fault. If neither changes, the fault is in this file.
//
// It comes off the page once the answer is in.
const TEST_OPTIONS = [
    { data: "one", label: "One" },
    { data: "two", label: "Two" },
    { data: "three", label: "Three" },
];
// Everything this page holds, and it is outside the component on purpose.
//
// Steam takes the panel apart and builds it again when the menu of a dropdown
// closes. Every useState inside it goes back to its first value at that
// moment, and that is the whole of the fault that four other changes did not
// find: the pick reached the machine, and the value this page held did not
// survive the pick.
//
// A test dropdown with three options and nothing behind it showed it. Its
// state went back to "one" at every pick, and no backend was involved.
//
// So the values live here, where a component that is built again reads the
// same ones, and `draw` below puts them on the screen.
const held = {
    status: null,
    strip: null,
    power: null,
    cec: null,
    gpu: null,
    // What a person picked, until the machine has answered.
    chosen: {},
    // What the sliders of the card hold, until a button sends them.
    wanted: {},
    said: "",
    keeping: "",
    busy: false,
    test: "one",
};
// Draws the page, and it always draws the one that is on the screen.
//
// A component that is built again brings a new way to draw itself, and the
// old one draws nothing at all. A command that started before the rebuild
// held the old one, so `busy` went to true, the panel was built again with
// `busy` still true, and the end of the command drew a component that was
// already gone. Every control on the page then stayed grey with nothing left
// to wake it.
//
// So the newest component puts its own here, and everything that finishes
// later draws through this.
let draw = () => undefined;
function Content() {
    // The one piece of state in this component, and it holds no value. A
    // component that is built again loses whatever it holds, so it holds
    // nothing: this draws what is in `held`.
    const [, redraw] = SP_REACT.useReducer((count) => count + 1, 0);
    draw = redraw;
    // One fetch when the page opens, and one after every change.
    //
    // There is no timer. There was one, and it asked for the cheap status,
    // which carries no state for the switches of the CEC toolkit. Every five
    // seconds it replaced the full answer with one that had none, and every
    // switch on the page went to off by itself.
    const refresh = async () => {
        const [whole, one, two, three, four] = await Promise.all([
            getFullStatus(),
            getArea("strip"),
            getArea("power"),
            getArea("cec"),
            getArea("gpu"),
        ]);
        held.status = whole;
        held.strip = one;
        held.power = two;
        held.cec = three;
        held.gpu = four;
        draw();
    };
    SP_REACT.useEffect(() => {
        // Not while a command runs. This is built again at every pick, and a
        // fetch that started then would answer with the value before the change
        // and land after the command that made it.
        if (!held.busy) {
            void refresh();
        }
        // Once, when this is built.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    // One place that changes something, so that every control reports a refusal
    // in the same way and nothing runs while something else does.
    const change = async (work) => {
        if (held.busy) {
            return;
        }
        held.busy = true;
        held.said = "";
        draw();
        try {
            const answer = await work();
            held.said = answer.ok ? "" : (answer.error ?? "That did not work.");
            await refresh();
            // The machine has answered, so what a person picked is not needed any
            // more. It goes whether the write worked or not: the answer of the
            // machine is the truth in both cases.
            held.chosen = {};
            held.wanted = {};
        }
        finally {
            held.busy = false;
            draw();
        }
    };
    const write = (area, updates) => void change(() => setArea(area, updates));
    // The value to draw: what was pressed, or what the machine holds.
    const shown = (area, key, value, fallback = "") => held.chosen[area + "." + key] ?? String(value ?? fallback);
    const pick = (area, key, value) => {
        held.chosen[area + "." + key] = value;
        draw();
        write(area, { [key]: value });
    };
    // The option lists, built one time for each answer of the command.
    //
    // They were rebuilt at every render before. A Dropdown that holds the
    // option it was given then holds an object that is no longer in the list it
    // was given, which is one way for a box to name a value that is gone.
    const rainbowOptions = SP_REACT.useMemo(() => options(held.strip?.offers?.RAINBOW_SHOWS), [held.strip]);
    const sceneOptions = SP_REACT.useMemo(() => options(held.strip?.offers?.DESKTOP_SCENE), [held.strip]);
    const governorOptions = SP_REACT.useMemo(() => options((held.power?.offers ?? {}).governors), [held.power]);
    const eppOptions = SP_REACT.useMemo(() => options((held.power?.offers ?? {}).epp), [held.power]);
    const knobs = (Array.isArray(held.gpu?.offers?.knobs) ? held.gpu?.offers?.knobs : []);
    // Send what the sliders hold, and then wait to be told to keep it.
    //
    // The daemon puts the card back by itself if nobody says so. That is not a
    // step to skip: a voltage offset that is too low hangs the card, and a hang
    // that was kept comes back at every boot.
    const send = async () => {
        if (held.busy || Object.keys(held.wanted).length === 0) {
            return;
        }
        held.busy = true;
        held.said = "";
        draw();
        try {
            const answer = await setArea("gpu", held.wanted);
            if (!answer.ok) {
                held.said = answer.error ?? "The card would not take it.";
                return;
            }
            held.keeping = "The card has it. Press Keep it, or the daemon puts the "
                + "card back by itself.";
        }
        finally {
            held.busy = false;
            draw();
        }
    };
    const keep = async () => {
        if (held.busy) {
            return;
        }
        held.busy = true;
        draw();
        try {
            const answer = await doAction("gpu-keep");
            if (!answer.ok) {
                held.said = answer.error ?? "The daemon did not take the confirmation.";
            }
            held.keeping = "";
            held.wanted = {};
            await refresh();
        }
        finally {
            held.busy = false;
            draw();
        }
    };
    const settings = (held.strip?.settings ?? {});
    const cpu = (held.power?.settings ?? {});
    const offered = (held.power?.offers ?? {});
    const switches = held.status?.cec_features ?? {};
    const installed = Boolean(held.status?.areas?.cec?.installed);
    // The switches of the toolkit, with the words that the panel uses for them.
    // The command answers with this list, so a switch that the toolkit gains
    // appears here with its own label and needs nothing written in this file.
    const features = (Array.isArray(held.cec?.offers?.features) ? held.cec?.offers?.features : []);
    const rainbow = shown("strip", "RAINBOW_SHOWS", settings.RAINBOW_SHOWS, "rainbow");
    const scene = shown("strip", "DESKTOP_SCENE", settings.DESKTOP_SCENE, "steam");
    const governor = shown("power", "CPU_GOVERNOR", cpu.CPU_GOVERNOR);
    const preference = shown("power", "CPU_EPP", cpu.CPU_EPP);
    return (SP_JSX.jsxs(SP_JSX.Fragment, { children: [(held.said !== "" || held.status?.sudo_rule === false) && (SP_JSX.jsxs(DFL.PanelSection, { children: [held.said !== "" && (SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx("div", { style: { fontSize: "0.8em", color: "#d85c5c" }, children: held.said }) })), held.status?.sudo_rule === false && (SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx("div", { style: { fontSize: "0.8em", color: "#d9a441" }, children: "Nothing here can change a setting. Install the panel again in Desktop Mode to get the rule that permits it." }) }))] })), SP_JSX.jsxs(DFL.PanelSection, { title: "LED bar", children: [SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(Choice, { label: "Rainbow slot", description: "What the rainbow entry of Steam's own LED menu shows. This is the one that acts in Game Mode.", options: rainbowOptions, value: rainbow, disabled: held.busy || !held.strip?.ok, onPick: (value) => pick("strip", "RAINBOW_SHOWS", value) }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(Choice, { label: "Desktop scene", description: "What the bar shows on the desktop. Game Mode belongs to Steam.", options: sceneOptions, value: scene, disabled: held.busy || !held.strip?.ok, onPick: (value) => pick("strip", "DESKTOP_SCENE", value) }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ToggleField, { label: "Notifications", description: "A flash for an achievement, a message or a friend who comes online.", checked: Boolean(settings.NOTIFY), disabled: held.busy || !held.strip?.ok, onChange: (on) => write("strip", { NOTIFY: on }) }) })] }), SP_JSX.jsx(DFL.PanelSection, { title: "CPU power", children: Number(offered.policies ?? 0) === 0 ? (SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx("div", { style: { fontSize: "0.8em" }, children: "This machine has no cpufreq, so there is nothing to set." }) })) : (SP_JSX.jsxs(SP_JSX.Fragment, { children: [SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(Choice, { label: "Governor", description: "How the clock is chosen.", options: governorOptions, value: governor, disabled: held.busy || !held.power?.ok, onPick: (value) => pick("power", "CPU_GOVERNOR", value) }) }), Array.isArray(offered.epp) && offered.epp.length > 0 && (SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(Choice, { label: "Energy preference", description: "A hint to the firmware about where in its range to sit. The performance governor pins it.", options: eppOptions, value: preference, disabled: held.busy || !held.power?.ok, onPick: (value) => pick("power", "CPU_EPP", value) }) }))] })) }), SP_JSX.jsx(DFL.PanelSection, { title: "Graphics card", children: !Boolean((held.gpu?.settings ?? {}).available) ? (SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx("div", { style: { fontSize: "0.8em" }, children: "LACT is not running, so there is nothing to set." }) })) : knobs.length === 0 ? (SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx("div", { style: { fontSize: "0.8em" }, children: "LACT reports no control for this card." }) })) : (SP_JSX.jsxs(SP_JSX.Fragment, { children: [knobs.map((knob) => (SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.SliderField, { label: knob.label + (knob.unit ? " (" + knob.unit + ")" : ""), value: held.wanted[knob.key] ?? knob.start, min: knob.min, max: knob.max, step: 1, notchTicksVisible: false, showValue: true, disabled: held.busy, onChange: (value) => {
                                    held.wanted[knob.key] = value;
                                    draw();
                                } }) }, knob.key))), held.keeping === "" ? (SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", disabled: held.busy || Object.keys(held.wanted).length === 0, onClick: () => void send(), children: "Send to the card" }) })) : (SP_JSX.jsxs(SP_JSX.Fragment, { children: [SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx("div", { style: { fontSize: "0.8em", color: "#d9a441" }, children: held.keeping }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", disabled: held.busy, onClick: () => void keep(), children: "Keep it" }) })] }))] })) }), SP_JSX.jsx(DFL.PanelSection, { title: "Television", children: !installed ? (SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx("div", { style: { fontSize: "0.8em" }, children: "The HDMI CEC toolkit is not installed. Install it from the panel in Desktop Mode." }) })) : (SP_JSX.jsx(SP_JSX.Fragment, { children: features.map((feature) => (SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ToggleField, { label: feature.label, description: feature.explains, checked: Boolean(switches[feature.name]), disabled: held.busy, onChange: (on) => write("cec", { [feature.name]: on }) }) }, feature.name))) })) }), SP_JSX.jsxs(DFL.PanelSection, { title: "Dropdown test", children: [SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.DropdownItem, { label: "Steam's own row", rgOptions: TEST_OPTIONS, selectedOption: held.test, onChange: (option) => {
                                held.test = String(option.data);
                                draw();
                            } }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(Choice, { label: "This page's row", description: "", options: TEST_OPTIONS, value: held.test, disabled: false, onPick: (value) => {
                                held.test = value;
                                draw();
                            } }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsxs("div", { style: { fontSize: "0.8em" }, children: [SP_JSX.jsxs("div", { children: ["What this page holds: ", held.test] }), SP_JSX.jsxs("div", { children: ["Rainbow slot: ", rainbow] }), SP_JSX.jsxs("div", { children: ["Governor: ", governor === "" ? "(not set)" : governor] })] }) })] })] }));
}
var index = definePlugin(() => ({
    name: "SteamOS Utility Center",
    titleView: SP_JSX.jsx("div", { children: "SteamOS Utility Center" }),
    content: SP_JSX.jsx(Content, {}),
    icon: SP_JSX.jsx(FaLightbulb, {}),
}));

export { index as default };
//# sourceMappingURL=index.js.map
