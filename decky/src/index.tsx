// SPDX-FileCopyrightText: 2026 caed1994
// SPDX-License-Identifier: GPL-3.0-or-later
//
// The Game Mode half of the SteamOS Utility Center.
//
// Every value here comes from steamos-utility-centerctl, and every change
// goes back to it. This file holds no rule of its own: what a setting is
// called, what a machine offers for it and what it refuses are all answers of
// that command. See server/steamos_utility_center/ctl.py.
//
// What is here and what is not follows one question: does a person do this
// while sitting on a sofa with a controller? A drive is added one time and a
// keyboard layout is set one time, so both are in the panel.

import { callable, definePlugin } from "@decky/api";
import {
  ButtonItem,
  Dropdown,
  Field,
  PanelSection,
  PanelSectionRow,
  SliderField,
  ToggleField,
} from "@decky/ui";
import { useEffect, useMemo, useReducer } from "react";
import { FaLightbulb } from "react-icons/fa";

// -- what the command answers -----------------------------------------------

type Answer = { ok: boolean; error?: string };

type Status = Answer & {
  sudo_rule?: boolean;
  // Which parts this machine has. The core is the panel and this command;
  // the LED bar, the CPU and GPU power, HDMI CEC and the drives are modules,
  // and a person takes each one from its own page in the panel. See
  // server/steamos_utility_center/modules.py.
  modules?: string[];
  areas?: { cec?: { installed?: boolean } };
  cec_features?: Record<string, boolean>;
};

type Feature = { name: string; label: string };

// One control of the graphics card, as the daemon reports it. The card
// decides which of these exist: a control with no range is a control that the
// card does not have. See lact.offered.
type Knob = {
  key: string;
  label: string;
  unit: string;
  min: number;
  max: number;
  start: number;
  value: number | null;
};

type Area = Answer & {
  settings?: Record<string, unknown>;
  offers?: Record<string, unknown>;
};

const getFullStatus = callable<[], Status>("get_full_status");
const getArea = callable<[string], Area>("get_area");
const setArea = callable<[string, Record<string, unknown>], Answer>("set_area");
const doAction = callable<[string], Answer>("do_action");

// The scenes of the strip, in words. The command answers with the names that
// the configuration file uses.
const SCENE_WORDS: Record<string, string> = {
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

function words(value: string): string {
  return SCENE_WORDS[value] ?? value;
}

function options(offered: unknown): { data: string; label: string }[] {
  if (!Array.isArray(offered)) {
    return [];
  }
  return offered.map((one) => String(one)).map((one) => ({
    data: one,
    label: words(one),
  }));
}

type Pick = { data: string; label: string };

function Choice(props: {
  label: string;
  options: Pick[];
  value: string;
  disabled: boolean;
  onPick: (value: string) => void;
}) {
  // Field and Dropdown, and not DropdownItem.
  //
  // DropdownItem is Steam's own settings row with Steam's own dropdown inside
  // it, and this project cannot see what that row does with a prop it does
  // not know. renderButtonValue is declared by @decky/ui on Dropdown, so it
  // goes to Dropdown here and passes through nothing on the way.
  //
  // Field is the same row that DropdownItem draws, so the page looks as it
  // did.
  return (
    <Field label={props.label} childrenContainerWidth="min">
      <Dropdown
        rgOptions={props.options}
        selectedOption={props.value}
        disabled={props.disabled}
        renderButtonValue={() =>
          props.options.find((one) => one.data === props.value)?.label
          ?? props.value}
        onChange={(option) => props.onPick(String(option.data))}
      />
    </Field>
  );
}

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
  status: null as Status | null,
  strip: null as Area | null,
  power: null as Area | null,
  cec: null as Area | null,
  gpu: null as Area | null,
  // What a person picked, until the machine has answered.
  chosen: {} as Record<string, string>,
  // What the sliders of the card hold, until a button sends them.
  wanted: {} as Record<string, number>,
  // What Cooling Boost was set to, until the machine has answered. null means
  // that the machine is the answer.
  boosting: null as boolean | null,
  said: "",
  keeping: "",
  busy: false,
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
let draw: () => void = () => undefined;

function Content() {
  // The one piece of state in this component, and it holds no value. A
  // component that is built again loses whatever it holds, so it holds
  // nothing: this draws what is in `held`.
  const [, redraw] = useReducer((count: number) => count + 1, 0);
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

  useEffect(() => {
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
  const change = async (work: () => Promise<Answer>) => {
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
    } finally {
      held.busy = false;
      draw();
    }
  };

  const write = (area: string, updates: Record<string, unknown>) =>
    void change(() => setArea(area, updates));

  // The value to draw: what was pressed, or what the machine holds.
  const shown = (area: string, key: string, value: unknown, fallback = "") =>
    held.chosen[area + "." + key] ?? String(value ?? fallback);

  const pick = (area: string, key: string, value: string) => {
    held.chosen[area + "." + key] = value;
    draw();
    write(area, { [key]: value });
  };

  // The option lists, built one time for each answer of the command.
  //
  // They were rebuilt at every render before. A Dropdown that holds the
  // option it was given then holds an object that is no longer in the list it
  // was given, which is one way for a box to name a value that is gone.
  const rainbowOptions = useMemo(
    () => options(held.strip?.offers?.RAINBOW_SHOWS), [held.strip]);
  const sceneOptions = useMemo(
    () => options(held.strip?.offers?.DESKTOP_SCENE), [held.strip]);
  const governorOptions = useMemo(
    () => options((held.power?.offers ?? {}).governors), [held.power]);
  const eppOptions = useMemo(
    () => options((held.power?.offers ?? {}).epp), [held.power]);

  const knobs = (
    Array.isArray(held.gpu?.offers?.knobs) ? held.gpu?.offers?.knobs : []
  ) as Knob[];

  // The card that LACT reports, and whether its fan is at full speed now.
  // Empty means a daemon that runs and has no card to control.
  const card = String((held.gpu?.offers ?? {}).gpu ?? "");
  const boosted = held.boosting ?? Boolean((held.gpu?.offers ?? {}).boost);

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
      held.keeping = "Press Keep it, or the card goes back by itself.";
    } finally {
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
    } finally {
      held.busy = false;
      draw();
    }
  };

  // Cooling Boost, which is not a setting of the card in the sense above.
  //
  // It goes to the command on its own and does not touch what the sliders
  // hold: a person who moved one and did not send it keeps it. The command
  // reads the document the daemon holds, changes the fan in it, and confirms
  // it at once. A fan at full speed hangs nothing, so there is nothing to
  // take back. See ctl._gpu_boost.
  const boostFan = async (on: boolean) => {
    if (held.busy) {
      return;
    }
    held.boosting = on;
    held.busy = true;
    held.said = "";
    draw();
    try {
      const answer = await doAction(on ? "gpu-boost-on" : "gpu-boost-off");
      if (!answer.ok) {
        held.said = answer.error ?? "The fan would not take it.";
      }
      // The card only, and not the whole page. Nothing else moved, and a
      // full fetch here would drop what the sliders hold.
      held.gpu = await getArea("gpu");
    } finally {
      held.boosting = null;
      held.busy = false;
      draw();
    }
  };

  const settings = (held.strip?.settings ?? {}) as Record<string, unknown>;
  const cpu = (held.power?.settings ?? {}) as Record<string, unknown>;
  const offered = (held.power?.offers ?? {}) as Record<string, unknown>;
  const switches = held.status?.cec_features ?? {};
  const installed = Boolean(held.status?.areas?.cec?.installed);

  // Whether this machine has one module. A section for a module that is not
  // installed is a row of controls that can apply nothing, and Game Mode is
  // the one screen where nobody can look in /var/lib to find out why.
  //
  // An unread status shows every section. This component draws before the
  // first answer arrives, and a plugin that hid each section until then would
  // open empty on every visit.
  const has = (name: string) =>
    held.status?.modules === undefined || held.status.modules.includes(name);

  // The switches of the toolkit, with the words that the panel uses for them.
  // The command answers with this list, so a switch that the toolkit gains
  // appears here with its own label and needs nothing written in this file.
  const features = (
    Array.isArray(held.cec?.offers?.features) ? held.cec?.offers?.features : []
  ) as Feature[];

  const rainbow = shown("strip", "RAINBOW_SHOWS", settings.RAINBOW_SHOWS,
                        "rainbow");
  const scene = shown("strip", "DESKTOP_SCENE", settings.DESKTOP_SCENE,
                      "steam");
  const governor = shown("power", "CPU_GOVERNOR", cpu.CPU_GOVERNOR);
  const preference = shown("power", "CPU_EPP", cpu.CPU_EPP);

  return (
    <>
      {/*
        Only what went wrong, and nothing when nothing did. A page that
        reports its own health at the top of every visit reports it to
        somebody who came to change one setting.
      */}
      {(held.said !== "" || held.status?.sudo_rule === false) && (
        <PanelSection>
          {held.said !== "" && (
            <PanelSectionRow>
              <div style={{ fontSize: "0.8em", color: "#d85c5c" }}>{held.said}</div>
            </PanelSectionRow>
          )}
          {held.status?.sudo_rule === false && (
            <PanelSectionRow>
              <div style={{ fontSize: "0.8em", color: "#d9a441" }}>
                Nothing here can change a setting. Install the panel again in
                Desktop Mode to get the rule that permits it.
              </div>
            </PanelSectionRow>
          )}
        </PanelSection>
      )}

      {held.status?.modules?.length === 0 && (
        <PanelSection>
          <PanelSectionRow>
            <div style={{ fontSize: "0.8em", color: "#d9a441" }}>
              No module is installed. The control panel in Desktop Mode
              installs the LED bar, the CPU and GPU power, HDMI CEC and the
              drives, each from its own page.
            </div>
          </PanelSectionRow>
        </PanelSection>
      )}

      {has("led") && (
      <PanelSection title="LED bar">
        <PanelSectionRow>
          <Choice
            label="Rainbow slot"
            options={rainbowOptions}
            value={rainbow}
            disabled={held.busy || !held.strip?.ok}
            onPick={(value) => pick("strip", "RAINBOW_SHOWS", value)}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <Choice
            label="Desktop scene"
            options={sceneOptions}
            value={scene}
            disabled={held.busy || !held.strip?.ok}
            onPick={(value) => pick("strip", "DESKTOP_SCENE", value)}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ToggleField
            label="Notifications"
            checked={Boolean(settings.NOTIFY)}
            disabled={held.busy || !held.strip?.ok}
            onChange={(on: boolean) => write("strip", { NOTIFY: on })}
          />
        </PanelSectionRow>
      </PanelSection>
      )}

      {has("power") && (
      <PanelSection title="CPU power">
        {Number(offered.policies ?? 0) === 0 ? (
          <PanelSectionRow>
            <div style={{ fontSize: "0.8em" }}>
              This machine has no cpufreq, so there is nothing to set.
            </div>
          </PanelSectionRow>
        ) : (
          <>
            <PanelSectionRow>
              <Choice
                label="Governor"
                options={governorOptions}
                value={governor}
                disabled={held.busy || !held.power?.ok}
                onPick={(value) => pick("power", "CPU_GOVERNOR", value)}
              />
            </PanelSectionRow>
            {Array.isArray(offered.epp) && offered.epp.length > 0 && (
              <PanelSectionRow>
                <Choice
                  label="Energy preference"
                  options={eppOptions}
                  value={preference}
                  disabled={held.busy || !held.power?.ok}
                  onPick={(value) => pick("power", "CPU_EPP", value)}
                />
              </PanelSectionRow>
            )}
          </>
        )}
      </PanelSection>
      )}

      {has("power") && (
      <PanelSection title="Graphics card">
        {!Boolean((held.gpu?.settings ?? {}).available) ? (
          <PanelSectionRow>
            <div style={{ fontSize: "0.8em" }}>
              LACT is not running, so there is nothing to set.
            </div>
          </PanelSectionRow>
        ) : (
          <>
            {knobs.length === 0 ? (
              <PanelSectionRow>
                <div style={{ fontSize: "0.8em" }}>
                  LACT reports no control for this card.
                </div>
              </PanelSectionRow>
            ) : (
              <>
                {knobs.map((knob) => (
                  <PanelSectionRow key={knob.key}>
                    <SliderField
                      label={knob.label + (knob.unit ? " (" + knob.unit + ")" : "")}
                      value={held.wanted[knob.key] ?? knob.start}
                      min={knob.min}
                      max={knob.max}
                      step={1}
                      notchTicksVisible={false}
                      showValue={true}
                      disabled={held.busy}
                      onChange={(value: number) => {
                        held.wanted[knob.key] = value;
                        draw();
                      }}
                    />
                  </PanelSectionRow>
                ))}
                {held.keeping === "" ? (
                  <PanelSectionRow>
                    <ButtonItem
                      layout="below"
                      disabled={held.busy || Object.keys(held.wanted).length === 0}
                      onClick={() => void send()}
                    >
                      Send to the card
                    </ButtonItem>
                  </PanelSectionRow>
                ) : (
                  <>
                    <PanelSectionRow>
                      <div style={{ fontSize: "0.8em", color: "#d9a441" }}>
                        {held.keeping}
                      </div>
                    </PanelSectionRow>
                    <PanelSectionRow>
                      <ButtonItem
                        layout="below"
                        disabled={held.busy}
                        onClick={() => void keep()}
                      >
                        Keep it
                      </ButtonItem>
                    </PanelSectionRow>
                  </>
                )}
              </>
            )}
            {/*
              Under the settings above, and not one of them. It writes the fan
              on its own, so it stays on a card that offers no control at all.

              A change that waits for Keep it holds it. Two writes to one
              document, with one of them unconfirmed, is a way to keep a
              voltage that nobody kept.
            */}
            {card !== "" && (
              <PanelSectionRow>
                <ToggleField
                  label="Cooling Boost"
                  checked={boosted}
                  disabled={held.busy || held.keeping !== ""}
                  onChange={(on: boolean) => void boostFan(on)}
                />
              </PanelSectionRow>
            )}
          </>
        )}
      </PanelSection>
      )}

      <PanelSection title="Television">
        {!installed ? (
          <PanelSectionRow>
            <div style={{ fontSize: "0.8em" }}>
              The HDMI CEC toolkit is not installed. Install it from the panel
              in Desktop Mode.
            </div>
          </PanelSectionRow>
        ) : (
          <>
            {features.map((feature) => (
              <PanelSectionRow key={feature.name}>
                <ToggleField
                  label={feature.label}
                  checked={Boolean(switches[feature.name])}
                  disabled={held.busy}
                  onChange={(on: boolean) =>
                    write("cec", { [feature.name]: on })}
                />
              </PanelSectionRow>
            ))}
          </>
        )}
      </PanelSection>
    </>
  );
}

export default definePlugin(() => ({
  name: "SteamOS Utility Center",
  titleView: <div>SteamOS Utility Center</div>,
  content: <Content />,
  icon: <FaLightbulb />,
}));
