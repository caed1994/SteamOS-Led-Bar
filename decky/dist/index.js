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

const getStatus = callable("get_status");
const getFullStatus = callable("get_full_status");
const getArea = callable("get_area");
const setArea = callable("set_area");
const doAction = callable("do_action");
// How often the cheap status is asked for. It opens files and starts no
// process, so this costs a game nothing.
const POLL_MS = 5000;
// The one switch that this page cannot operate. It controls a unit of root,
// and Game Mode has nobody to answer a password.
const BY_HAND = ["resume-wake"];
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
function Content() {
    const [status, setStatus] = SP_REACT.useState(null);
    const [strip, setStrip] = SP_REACT.useState(null);
    const [power, setPower] = SP_REACT.useState(null);
    const [cec, setCec] = SP_REACT.useState(null);
    const [busy, setBusy] = SP_REACT.useState(false);
    const [said, setSaid] = SP_REACT.useState("");
    // The cheap half, on a timer. The expensive half is asked for when the page
    // opens and again after a change that can move one of its answers.
    const refreshCheap = SP_REACT.useCallback(async () => {
        setStatus(await getStatus());
    }, []);
    const refreshAll = SP_REACT.useCallback(async () => {
        const [whole, one, two, three] = await Promise.all([
            getFullStatus(),
            getArea("strip"),
            getArea("power"),
            getArea("cec"),
        ]);
        setStatus(whole);
        setStrip(one);
        setPower(two);
        setCec(three);
    }, []);
    SP_REACT.useEffect(() => {
        void refreshAll();
        const timer = setInterval(() => void refreshCheap(), POLL_MS);
        return () => clearInterval(timer);
    }, [refreshAll, refreshCheap]);
    // One place that changes something, so that every button reports a refusal
    // in the same way and nothing runs while something else does.
    const change = SP_REACT.useCallback(async (work) => {
        if (busy) {
            return;
        }
        setBusy(true);
        setSaid("");
        try {
            const answer = await work();
            if (!answer.ok) {
                setSaid(answer.error ?? "That did not work.");
            }
            await refreshAll();
        }
        finally {
            setBusy(false);
        }
    }, [busy, refreshAll]);
    const write = (area, updates) => void change(() => setArea(area, updates));
    const ready = status?.ready;
    const settings = (strip?.settings ?? {});
    const cpu = (power?.settings ?? {});
    const offered = (power?.offers ?? {});
    const drives = status?.areas?.drives?.drives ?? [];
    const switches = status?.cec_features ?? {};
    const installed = Boolean(status?.areas?.cec?.installed);
    // The switches of the toolkit, with the words that the panel uses for them.
    // The command answers with this list, so a switch that the toolkit gains
    // appears here with its own label and needs nothing written in this file.
    const features = (Array.isArray(cec?.offers?.features) ? cec?.offers?.features : []);
    return (SP_JSX.jsxs(SP_JSX.Fragment, { children: [SP_JSX.jsxs(DFL.PanelSection, { title: "This machine", children: [SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx("div", { style: { fontSize: "0.8em", lineHeight: "1.5em" }, children: status && !status.ok ? (SP_JSX.jsx("div", { style: { color: "#d85c5c" }, children: status.error })) : (SP_JSX.jsxs(SP_JSX.Fragment, { children: [SP_JSX.jsxs("div", { children: ["LED bar: ", ready?.module ? "ready" : "the kernel module is not loaded"] }), SP_JSX.jsxs("div", { children: ["HDMI CEC: ", ready?.cec ? "installed" : "not installed"] }), SP_JSX.jsxs("div", { children: ["Drives: ", ready ? `${ready.mounted} of ${ready.drives} mounted` : "reading"] }), status?.sudo_rule === false && (SP_JSX.jsx("div", { style: { color: "#d9a441" }, children: "Nothing here can change a setting. Install the panel again in Desktop Mode to get the rule that permits it." }))] })) }) }), said !== "" && (SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx("div", { style: { fontSize: "0.8em", color: "#d85c5c" }, children: said }) }))] }), SP_JSX.jsxs(DFL.PanelSection, { title: "LED bar", children: [SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.DropdownItem, { label: "Rainbow slot", description: "What the rainbow entry of Steam's own LED menu shows. This is the one that acts in Game Mode.", rgOptions: options(strip?.offers?.RAINBOW_SHOWS), selectedOption: String(settings.RAINBOW_SHOWS ?? "rainbow"), disabled: busy || !strip?.ok, onChange: (option) => write("strip", { RAINBOW_SHOWS: String(option.data) }) }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.DropdownItem, { label: "Desktop scene", description: "What the bar shows on the desktop. Game Mode belongs to Steam.", rgOptions: options(strip?.offers?.DESKTOP_SCENE), selectedOption: String(settings.DESKTOP_SCENE ?? "steam"), disabled: busy || !strip?.ok, onChange: (option) => write("strip", { DESKTOP_SCENE: String(option.data) }) }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.SliderField, { label: "Brightness", description: "The top of the range for every effect.", value: Number(settings.MAX_BRIGHTNESS ?? 255), min: 0, max: 255, step: 5, notchTicksVisible: false, showValue: true, disabled: busy || !strip?.ok, onChange: (value) => write("strip", { MAX_BRIGHTNESS: value }) }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ToggleField, { label: "Notifications", description: "A flash for an achievement, a message or a friend who comes online.", checked: Boolean(settings.NOTIFY), disabled: busy || !strip?.ok, onChange: (on) => write("strip", { NOTIFY: on }) }) })] }), SP_JSX.jsx(DFL.PanelSection, { title: "CPU power", children: Number(offered.policies ?? 0) === 0 ? (SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx("div", { style: { fontSize: "0.8em" }, children: "This machine has no cpufreq, so there is nothing to set." }) })) : (SP_JSX.jsxs(SP_JSX.Fragment, { children: [SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.DropdownItem, { label: "Governor", description: "How the clock is chosen.", rgOptions: options(offered.governors), selectedOption: String(cpu.CPU_GOVERNOR ?? ""), disabled: busy || !power?.ok, onChange: (option) => write("power", { CPU_GOVERNOR: String(option.data) }) }) }), Array.isArray(offered.epp) && offered.epp.length > 0 && (SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.DropdownItem, { label: "Energy preference", description: "A hint to the firmware about where in its range to sit. The performance governor pins it.", rgOptions: options(offered.epp), selectedOption: String(cpu.CPU_EPP ?? "default"), disabled: busy || !power?.ok, onChange: (option) => write("power", { CPU_EPP: String(option.data) }) }) }))] })) }), SP_JSX.jsx(DFL.PanelSection, { title: "Television", children: !installed ? (SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx("div", { style: { fontSize: "0.8em" }, children: "The HDMI CEC toolkit is not installed. Install it from the panel in Desktop Mode." }) })) : (SP_JSX.jsxs(SP_JSX.Fragment, { children: [SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", disabled: busy, onClick: () => void change(() => doAction("cec-wake")), children: "Turn the television on" }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", disabled: busy, onClick: () => void change(() => doAction("cec-standby")), children: "Send standby" }) }), features.map((feature) => (SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ToggleField, { label: feature.label, description: BY_HAND.includes(feature.name)
                                    ? "This one is set in the panel: it controls a unit of root, and Game Mode has nobody to ask for a password."
                                    : feature.explains, checked: Boolean(switches[feature.name]), disabled: busy || BY_HAND.includes(feature.name), onChange: (on) => write("cec", { [feature.name]: on }) }) }, feature.name)))] })) }), SP_JSX.jsx(DFL.PanelSection, { title: "Drives", children: drives.length === 0 ? (SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx("div", { style: { fontSize: "0.8em" }, children: "No drive is configured. Add one from the panel in Desktop Mode." }) })) : (SP_JSX.jsxs(SP_JSX.Fragment, { children: [drives.map((drive) => (SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsxs("div", { style: { fontSize: "0.8em", display: "flex", justifyContent: "space-between" }, children: [SP_JSX.jsx("span", { children: drive.where }), SP_JSX.jsx("span", { style: { color: drive.mounted ? "#59bf6b" : "#8a98a8" }, children: drive.mounted ? "mounted" : "not mounted" })] }) }, drive.uuid))), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", disabled: busy, onClick: () => void change(() => doAction("repair-drives")), children: "Mount them again" }) })] })) })] }));
}
var index = definePlugin(() => ({
    name: "SteamOS Utility Center",
    titleView: SP_JSX.jsx("div", { children: "SteamOS Utility Center" }),
    content: SP_JSX.jsx(Content, {}),
    icon: SP_JSX.jsx(FaLightbulb, {}),
}));

export { index as default };
//# sourceMappingURL=index.js.map
