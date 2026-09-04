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
  DropdownItem,
  Field,
  PanelSection,
  PanelSectionRow,
  SliderField,
  ToggleField,
} from "@decky/ui";
import { useCallback, useEffect, useMemo, useState } from "react";
import { FaLightbulb } from "react-icons/fa";

// -- what the command answers -----------------------------------------------

type Answer = { ok: boolean; error?: string };

type Status = Answer & {
  sudo_rule?: boolean;
  areas?: { cec?: { installed?: boolean } };
  cec_features?: Record<string, boolean>;
};

type Feature = { name: string; label: string; explains: string };

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
  description: string;
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
    <Field label={props.label} description={props.description}
           childrenContainerWidth="min">
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

// Three options and nothing behind them, for one question: does a dropdown
// follow the state of this page at all?
//
// Four changes to the real controls did nothing, and each of them was a guess
// about a component that cannot be run outside Game Mode. This answers the
// guess. If the line below the box changes and the box does not, the box is
// the fault. If neither changes, the fault is in this file.
//
// It comes off the page once the answer is in.
const TEST_OPTIONS: Pick[] = [
  { data: "one", label: "One" },
  { data: "two", label: "Two" },
  { data: "three", label: "Three" },
];

function Content() {
  const [status, setStatus] = useState<Status | null>(null);
  const [strip, setStrip] = useState<Area | null>(null);
  const [power, setPower] = useState<Area | null>(null);
  const [cec, setCec] = useState<Area | null>(null);
  const [gpu, setGpu] = useState<Area | null>(null);
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState("");

  // What a person picked, before the machine has answered.
  //
  // The dropdown of Steam keeps the option it was built with, so a new value
  // in its props does not move it. This holds the choice, and Choice draws
  // the closed box from it, so a box says what was pressed at the moment it
  // is pressed rather than after a command has run. A refresh takes it away
  // again, and the machine's own answer is what stays.
  const [chosen, setChosen] = useState<Record<string, string>>({});

  // The controls of the card, and whether one is waiting to be kept.
  //
  // A slider here writes nothing while it moves. The daemon takes a change
  // back after some seconds unless it is told to keep it, and a slider that
  // sent at every step would start that clock at every step. So the sliders
  // hold a value, one button sends them, and a second button keeps them.
  const [wanted, setWanted] = useState<Record<string, number>>({});
  const [keeping, setKeeping] = useState("");
  const [test, setTest] = useState("one");

  // One fetch when the page opens, and one after every change.
  //
  // There is no timer. There was one, and it asked for the cheap status,
  // which carries no state for the switches of the CEC toolkit. Every five
  // seconds it replaced the full answer with one that had none, and every
  // switch on the page went to off by itself.
  const refresh = useCallback(async () => {
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
    setGpu(await getArea("gpu"));
    setChosen({});
    setWanted({});
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // One place that changes something, so that every control reports a refusal
  // in the same way and nothing runs while something else does.
  const change = useCallback(
    async (work: () => Promise<Answer>) => {
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
        await refresh();
      } finally {
        setBusy(false);
      }
    },
    [busy, refresh],
  );

  const write = (area: string, updates: Record<string, unknown>) =>
    void change(() => setArea(area, updates));

  // The value to draw: what was pressed, or what the machine holds.
  const shown = (area: string, key: string, held: unknown, fallback = "") =>
    chosen[area + "." + key] ?? String(held ?? fallback);

  const pick = (area: string, key: string, value: string) => {
    setChosen((was) => ({ ...was, [area + "." + key]: value }));
    write(area, { [key]: value });
  };

  // The option lists, built one time for each answer of the command.
  //
  // They were rebuilt at every render before. A Dropdown that holds the
  // option it was given then holds an object that is no longer in the list it
  // was given, which is one way for a box to name a value that is gone.
  const rainbowOptions = useMemo(
    () => options(strip?.offers?.RAINBOW_SHOWS), [strip]);
  const sceneOptions = useMemo(
    () => options(strip?.offers?.DESKTOP_SCENE), [strip]);
  const governorOptions = useMemo(
    () => options((power?.offers ?? {}).governors), [power]);
  const eppOptions = useMemo(
    () => options((power?.offers ?? {}).epp), [power]);

  const knobs = (
    Array.isArray(gpu?.offers?.knobs) ? gpu?.offers?.knobs : []
  ) as Knob[];

  // Send what the sliders hold, and then wait to be told to keep it.
  //
  // The daemon puts the card back by itself if nobody says so. That is not a
  // step to skip: a voltage offset that is too low hangs the card, and a hang
  // that was kept comes back at every boot.
  const send = async () => {
    if (busy || Object.keys(wanted).length === 0) {
      return;
    }
    setBusy(true);
    setSaid("");
    try {
      const answer = await setArea("gpu", wanted);
      if (!answer.ok) {
        setSaid(answer.error ?? "The card would not take it.");
        return;
      }
      setKeeping("The card has it. Press Keep it, or the daemon puts the "
                 + "card back by itself.");
    } finally {
      setBusy(false);
    }
  };

  const keep = async () => {
    if (busy) {
      return;
    }
    setBusy(true);
    try {
      const answer = await doAction("gpu-keep");
      if (!answer.ok) {
        setSaid(answer.error ?? "The daemon did not take the confirmation.");
      }
      setKeeping("");
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  const settings = (strip?.settings ?? {}) as Record<string, unknown>;
  const cpu = (power?.settings ?? {}) as Record<string, unknown>;
  const offered = (power?.offers ?? {}) as Record<string, unknown>;
  const switches = status?.cec_features ?? {};
  const installed = Boolean(status?.areas?.cec?.installed);

  // The switches of the toolkit, with the words that the panel uses for them.
  // The command answers with this list, so a switch that the toolkit gains
  // appears here with its own label and needs nothing written in this file.
  const features = (
    Array.isArray(cec?.offers?.features) ? cec?.offers?.features : []
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
      {(said !== "" || status?.sudo_rule === false) && (
        <PanelSection>
          {said !== "" && (
            <PanelSectionRow>
              <div style={{ fontSize: "0.8em", color: "#d85c5c" }}>{said}</div>
            </PanelSectionRow>
          )}
          {status?.sudo_rule === false && (
            <PanelSectionRow>
              <div style={{ fontSize: "0.8em", color: "#d9a441" }}>
                Nothing here can change a setting. Install the panel again in
                Desktop Mode to get the rule that permits it.
              </div>
            </PanelSectionRow>
          )}
        </PanelSection>
      )}

      <PanelSection title="LED bar">
        <PanelSectionRow>
          <Choice
            label="Rainbow slot"
            description="What the rainbow entry of Steam's own LED menu shows. This is the one that acts in Game Mode."
            options={rainbowOptions}
            value={rainbow}
            disabled={busy || !strip?.ok}
            onPick={(value) => pick("strip", "RAINBOW_SHOWS", value)}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <Choice
            label="Desktop scene"
            description="What the bar shows on the desktop. Game Mode belongs to Steam."
            options={sceneOptions}
            value={scene}
            disabled={busy || !strip?.ok}
            onPick={(value) => pick("strip", "DESKTOP_SCENE", value)}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ToggleField
            label="Notifications"
            description="A flash for an achievement, a message or a friend who comes online."
            checked={Boolean(settings.NOTIFY)}
            disabled={busy || !strip?.ok}
            onChange={(on: boolean) => write("strip", { NOTIFY: on })}
          />
        </PanelSectionRow>
      </PanelSection>

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
                description="How the clock is chosen."
                options={governorOptions}
                value={governor}
                disabled={busy || !power?.ok}
                onPick={(value) => pick("power", "CPU_GOVERNOR", value)}
              />
            </PanelSectionRow>
            {Array.isArray(offered.epp) && offered.epp.length > 0 && (
              <PanelSectionRow>
                <Choice
                  label="Energy preference"
                  description="A hint to the firmware about where in its range to sit. The performance governor pins it."
                  options={eppOptions}
                  value={preference}
                  disabled={busy || !power?.ok}
                  onPick={(value) => pick("power", "CPU_EPP", value)}
                />
              </PanelSectionRow>
            )}
          </>
        )}
      </PanelSection>

      <PanelSection title="Graphics card">
        {!Boolean((gpu?.settings ?? {}).available) ? (
          <PanelSectionRow>
            <div style={{ fontSize: "0.8em" }}>
              LACT is not running, so there is nothing to set.
            </div>
          </PanelSectionRow>
        ) : knobs.length === 0 ? (
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
                  value={wanted[knob.key] ?? knob.start}
                  min={knob.min}
                  max={knob.max}
                  step={1}
                  notchTicksVisible={false}
                  showValue={true}
                  disabled={busy}
                  onChange={(value: number) =>
                    setWanted((was) => ({ ...was, [knob.key]: value }))}
                />
              </PanelSectionRow>
            ))}
            {keeping === "" ? (
              <PanelSectionRow>
                <ButtonItem
                  layout="below"
                  disabled={busy || Object.keys(wanted).length === 0}
                  onClick={() => void send()}
                >
                  Send to the card
                </ButtonItem>
              </PanelSectionRow>
            ) : (
              <>
                <PanelSectionRow>
                  <div style={{ fontSize: "0.8em", color: "#d9a441" }}>
                    {keeping}
                  </div>
                </PanelSectionRow>
                <PanelSectionRow>
                  <ButtonItem
                    layout="below"
                    disabled={busy}
                    onClick={() => void keep()}
                  >
                    Keep it
                  </ButtonItem>
                </PanelSectionRow>
              </>
            )}
          </>
        )}
      </PanelSection>

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
                  description={feature.explains}
                  checked={Boolean(switches[feature.name])}
                  disabled={busy}
                  onChange={(on: boolean) =>
                    write("cec", { [feature.name]: on })}
                />
              </PanelSectionRow>
            ))}
          </>
        )}
      </PanelSection>
      <PanelSection title="Dropdown test">
        <PanelSectionRow>
          <DropdownItem
            label="Steam's own row"
            rgOptions={TEST_OPTIONS}
            selectedOption={test}
            onChange={(option) => setTest(String(option.data))}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <Choice
            label="This page's row"
            description=""
            options={TEST_OPTIONS}
            value={test}
            disabled={false}
            onPick={setTest}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <div style={{ fontSize: "0.8em" }}>
            <div>What this page holds: {test}</div>
            <div>Rainbow slot: {rainbow}</div>
            <div>Governor: {governor === "" ? "(not set)" : governor}</div>
          </div>
        </PanelSectionRow>
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
