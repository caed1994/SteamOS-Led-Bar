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
  DropdownItem,
  PanelSection,
  PanelSectionRow,
  ToggleField,
} from "@decky/ui";
import { useCallback, useEffect, useState } from "react";
import { FaLightbulb } from "react-icons/fa";

// -- what the command answers -----------------------------------------------

type Answer = { ok: boolean; error?: string };

type Status = Answer & {
  sudo_rule?: boolean;
  areas?: { cec?: { installed?: boolean } };
  cec_features?: Record<string, boolean>;
};

type Feature = { name: string; label: string; explains: string };

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

function Content() {
  const [status, setStatus] = useState<Status | null>(null);
  const [strip, setStrip] = useState<Area | null>(null);
  const [power, setPower] = useState<Area | null>(null);
  const [cec, setCec] = useState<Area | null>(null);
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState("");

  // What a person picked, before the machine has answered.
  //
  // A DropdownItem takes its option when it is built and keeps it, so a new
  // value in the props does not move it. This holds the choice, the key below
  // carries it, and the box thus says what was pressed at the moment it is
  // pressed rather than after a command has run. A refresh takes it away
  // again, and the machine's own answer is what stays.
  const [chosen, setChosen] = useState<Record<string, string>>({});

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
    setChosen({});
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
          {/*
            The key holds the value. Without it a DropdownItem keeps the
            option it was built with, so the effect changed and the box went
            on naming the one before it.
          */}
          <DropdownItem
            key={"rainbow-" + rainbow}
            label="Rainbow slot"
            description="What the rainbow entry of Steam's own LED menu shows. This is the one that acts in Game Mode."
            rgOptions={options(strip?.offers?.RAINBOW_SHOWS)}
            selectedOption={rainbow}
            disabled={busy || !strip?.ok}
            onChange={(option) =>
              pick("strip", "RAINBOW_SHOWS", String(option.data))}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <DropdownItem
            key={"scene-" + scene}
            label="Desktop scene"
            description="What the bar shows on the desktop. Game Mode belongs to Steam."
            rgOptions={options(strip?.offers?.DESKTOP_SCENE)}
            selectedOption={scene}
            disabled={busy || !strip?.ok}
            onChange={(option) =>
              pick("strip", "DESKTOP_SCENE", String(option.data))}
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
              <DropdownItem
                key={"governor-" + governor}
                label="Governor"
                description="How the clock is chosen."
                rgOptions={options(offered.governors)}
                selectedOption={governor}
                disabled={busy || !power?.ok}
                onChange={(option) =>
                  pick("power", "CPU_GOVERNOR", String(option.data))}
              />
            </PanelSectionRow>
            {Array.isArray(offered.epp) && offered.epp.length > 0 && (
              <PanelSectionRow>
                <DropdownItem
                  key={"epp-" + preference}
                  label="Energy preference"
                  description="A hint to the firmware about where in its range to sit. The performance governor pins it."
                  rgOptions={options(offered.epp)}
                  selectedOption={preference}
                  disabled={busy || !power?.ok}
                  onChange={(option) =>
                    pick("power", "CPU_EPP", String(option.data))}
                />
              </PanelSectionRow>
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
            <PanelSectionRow>
              <ButtonItem
                layout="below"
                disabled={busy}
                onClick={() => void change(() => doAction("cec-wake"))}
              >
                Turn the television on
              </ButtonItem>
            </PanelSectionRow>
            <PanelSectionRow>
              <ButtonItem
                layout="below"
                disabled={busy}
                onClick={() => void change(() => doAction("cec-standby"))}
              >
                Send standby
              </ButtonItem>
            </PanelSectionRow>
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
    </>
  );
}

export default definePlugin(() => ({
  name: "SteamOS Utility Center",
  titleView: <div>SteamOS Utility Center</div>,
  content: <Content />,
  icon: <FaLightbulb />,
}));
