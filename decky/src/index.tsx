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
// while sitting on a sofa with a controller? The keyboard layout is set one
// time and is in the panel for that reason.

import { callable, definePlugin } from "@decky/api";
import {
  ButtonItem,
  DropdownItem,
  PanelSection,
  PanelSectionRow,
  SliderField,
  ToggleField,
} from "@decky/ui";
import { useCallback, useEffect, useState } from "react";
import { FaLightbulb } from "react-icons/fa";

// -- what the command answers -----------------------------------------------

type Answer = { ok: boolean; error?: string };

type Ready = {
  module: boolean;
  cec: boolean;
  mounted: number;
  drives: number;
};

type Drive = {
  uuid: string;
  where: string;
  type: string;
  mounted: boolean;
};

type Status = Answer & {
  ready?: Ready;
  sudo_rule?: boolean;
  areas?: {
    drives?: { drives?: Drive[] };
    cec?: { installed?: boolean };
  };
  cec_features?: Record<string, boolean>;
  full?: boolean;
};

type Feature = { name: string; label: string; explains: string };

type Area = Answer & {
  settings?: Record<string, unknown>;
  offers?: Record<string, unknown>;
};

const getStatus = callable<[], Status>("get_status");
const getFullStatus = callable<[], Status>("get_full_status");
const getArea = callable<[string], Area>("get_area");
const setArea = callable<[string, Record<string, unknown>], Answer>("set_area");
const doAction = callable<[string], Answer>("do_action");

// How often the cheap status is asked for. It opens files and starts no
// process, so this costs a game nothing.
const POLL_MS = 5000;

// The one switch that this page cannot operate. It controls a unit of root,
// and Game Mode has nobody to answer a password.
const BY_HAND = ["resume-wake"];

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

  // The cheap half, on a timer. The expensive half is asked for when the page
  // opens and again after a change that can move one of its answers.
  const refreshCheap = useCallback(async () => {
    setStatus(await getStatus());
  }, []);

  const refreshAll = useCallback(async () => {
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

  useEffect(() => {
    void refreshAll();
    const timer = setInterval(() => void refreshCheap(), POLL_MS);
    return () => clearInterval(timer);
  }, [refreshAll, refreshCheap]);

  // One place that changes something, so that every button reports a refusal
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
        await refreshAll();
      } finally {
        setBusy(false);
      }
    },
    [busy, refreshAll],
  );

  const write = (area: string, updates: Record<string, unknown>) =>
    void change(() => setArea(area, updates));

  const ready = status?.ready;
  const settings = (strip?.settings ?? {}) as Record<string, unknown>;
  const cpu = (power?.settings ?? {}) as Record<string, unknown>;
  const offered = (power?.offers ?? {}) as Record<string, unknown>;
  const drives = status?.areas?.drives?.drives ?? [];
  const switches = status?.cec_features ?? {};
  const installed = Boolean(status?.areas?.cec?.installed);

  // The switches of the toolkit, with the words that the panel uses for them.
  // The command answers with this list, so a switch that the toolkit gains
  // appears here with its own label and needs nothing written in this file.
  const features = (
    Array.isArray(cec?.offers?.features) ? cec?.offers?.features : []
  ) as Feature[];

  return (
    <>
      <PanelSection title="This machine">
        <PanelSectionRow>
          <div style={{ fontSize: "0.8em", lineHeight: "1.5em" }}>
            {status && !status.ok ? (
              <div style={{ color: "#d85c5c" }}>{status.error}</div>
            ) : (
              <>
                <div>
                  LED bar: {ready?.module ? "ready" : "the kernel module is not loaded"}
                </div>
                <div>HDMI CEC: {ready?.cec ? "installed" : "not installed"}</div>
                <div>
                  Drives: {ready ? `${ready.mounted} of ${ready.drives} mounted` : "reading"}
                </div>
                {status?.sudo_rule === false && (
                  <div style={{ color: "#d9a441" }}>
                    Nothing here can change a setting. Install the panel again
                    in Desktop Mode to get the rule that permits it.
                  </div>
                )}
              </>
            )}
          </div>
        </PanelSectionRow>
        {said !== "" && (
          <PanelSectionRow>
            <div style={{ fontSize: "0.8em", color: "#d85c5c" }}>{said}</div>
          </PanelSectionRow>
        )}
      </PanelSection>

      <PanelSection title="LED bar">
        <PanelSectionRow>
          <DropdownItem
            label="Rainbow slot"
            description="What the rainbow entry of Steam's own LED menu shows. This is the one that acts in Game Mode."
            rgOptions={options(strip?.offers?.RAINBOW_SHOWS)}
            selectedOption={String(settings.RAINBOW_SHOWS ?? "rainbow")}
            disabled={busy || !strip?.ok}
            onChange={(option) => write("strip", { RAINBOW_SHOWS: String(option.data) })}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <DropdownItem
            label="Desktop scene"
            description="What the bar shows on the desktop. Game Mode belongs to Steam."
            rgOptions={options(strip?.offers?.DESKTOP_SCENE)}
            selectedOption={String(settings.DESKTOP_SCENE ?? "steam")}
            disabled={busy || !strip?.ok}
            onChange={(option) => write("strip", { DESKTOP_SCENE: String(option.data) })}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <SliderField
            label="Brightness"
            description="The top of the range for every effect."
            value={Number(settings.MAX_BRIGHTNESS ?? 255)}
            min={0}
            max={255}
            step={5}
            notchTicksVisible={false}
            showValue={true}
            disabled={busy || !strip?.ok}
            onChange={(value: number) => write("strip", { MAX_BRIGHTNESS: value })}
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
                label="Governor"
                description="How the clock is chosen."
                rgOptions={options(offered.governors)}
                selectedOption={String(cpu.CPU_GOVERNOR ?? "")}
                disabled={busy || !power?.ok}
                onChange={(option) => write("power", { CPU_GOVERNOR: String(option.data) })}
              />
            </PanelSectionRow>
            {Array.isArray(offered.epp) && offered.epp.length > 0 && (
              <PanelSectionRow>
                <DropdownItem
                  label="Energy preference"
                  description="A hint to the firmware about where in its range to sit. The performance governor pins it."
                  rgOptions={options(offered.epp)}
                  selectedOption={String(cpu.CPU_EPP ?? "default")}
                  disabled={busy || !power?.ok}
                  onChange={(option) => write("power", { CPU_EPP: String(option.data) })}
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
                  description={
                    BY_HAND.includes(feature.name)
                      ? "This one is set in the panel: it controls a unit of root, and Game Mode has nobody to ask for a password."
                      : feature.explains
                  }
                  checked={Boolean(switches[feature.name])}
                  disabled={busy || BY_HAND.includes(feature.name)}
                  onChange={(on: boolean) => write("cec", { [feature.name]: on })}
                />
              </PanelSectionRow>
            ))}
          </>
        )}
      </PanelSection>

      <PanelSection title="Drives">
        {drives.length === 0 ? (
          <PanelSectionRow>
            <div style={{ fontSize: "0.8em" }}>
              No drive is configured. Add one from the panel in Desktop Mode.
            </div>
          </PanelSectionRow>
        ) : (
          <>
            {drives.map((drive) => (
              <PanelSectionRow key={drive.uuid}>
                <div style={{ fontSize: "0.8em", display: "flex", justifyContent: "space-between" }}>
                  <span>{drive.where}</span>
                  <span style={{ color: drive.mounted ? "#59bf6b" : "#8a98a8" }}>
                    {drive.mounted ? "mounted" : "not mounted"}
                  </span>
                </div>
              </PanelSectionRow>
            ))}
            <PanelSectionRow>
              <ButtonItem
                layout="below"
                disabled={busy}
                onClick={() => void change(() => doAction("repair-drives"))}
              >
                Mount them again
              </ButtonItem>
            </PanelSectionRow>
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
