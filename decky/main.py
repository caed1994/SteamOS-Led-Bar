# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""The Game Mode half of the SteamOS Utility Center.

This holds no rules. Every method starts steamos-utility-centerctl and gives
back what it printed. The rules are in the same modules that the panel calls,
and one copy of them is one answer to each question. See
server/steamos_utility_center/ctl.py.

The shape of this file, and the three environment variables that it corrects,
come from the Decky plugin of the SteamOS CEC Toolkit by Twsts, which is MIT.
That plugin is the other front end of the toolkit in cec-toolkit/. See
cec-toolkit/ORIGIN.
"""

import asyncio
import json
import os

import decky

# The command, in /var/lib and not in /usr/local/bin.
#
# Both are there after an installation. /usr/local/bin is on the read-only
# root filesystem of SteamOS, and an update takes it away with the kernel
# module. /var/lib is a partition of its own and it stays.
CTL = "/var/lib/steamos-utility-center/steamos-utility-centerctl"


class Plugin:

    async def _ctl(self, *args: str) -> dict:
        """Runs one command and returns the object that it printed.

        Decky starts a plugin with no session around it, and three variables
        of that session are necessary here.

        XDG_RUNTIME_DIR and DBUS_SESSION_BUS_ADDRESS: without them every
        `systemctl --user` of the CEC toolkit fails, and each switch on that
        page is a user unit.

        LD_LIBRARY_PATH is removed. Decky runs inside the environment of
        Steam, which points that variable at the libraries of the Steam
        runtime. A program of the system that inherits it loads the wrong
        libraries, and python is such a program.
        """
        uid = os.getuid()
        where = os.environ.copy()
        where.update({
            "HOME": os.path.expanduser("~"),
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "SYSTEMD_PAGER": "",
            "XDG_RUNTIME_DIR": "/run/user/%d" % uid,
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/%d/bus" % uid,
        })
        where.pop("LD_LIBRARY_PATH", None)

        if not os.path.exists(CTL):
            return {"ok": False,
                    "error": "%s is not there. Install the SteamOS Utility "
                             "Center in Desktop Mode first." % CTL}

        decky.logger.info("running: %s", " ".join((CTL,) + args))
        process = await asyncio.create_subprocess_exec(
            CTL, *args, env=where,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await process.communicate()
        printed = out.decode("utf-8", errors="replace").strip()
        complained = err.decode("utf-8", errors="replace").strip()
        decky.logger.info("exit %s, stderr %r", process.returncode, complained)

        # The command prints one object and nothing else, and it prints one
        # for a refusal also. Text that this cannot read thus means that the
        # command did not run at all.
        try:
            return json.loads(printed)
        except ValueError:
            return {"ok": False,
                    "error": complained or printed or
                             "the command printed nothing"}

    async def get_status(self) -> dict:
        """The half that costs no process. A page asks for this on a timer."""
        return await self._ctl("status")

    async def get_full_status(self) -> dict:
        """The half that needs systemctl, lsblk and the toolkit.

        A page asks for this one time when it opens, and again after a change
        that moves one of its answers.
        """
        return await self._ctl("status", "--full")

    async def get_area(self, area: str) -> dict:
        """One area: what is set, and what this machine offers for it."""
        return await self._ctl("get", area)

    async def set_area(self, area: str, updates: dict) -> dict:
        """Writes a change into one area, and puts it into effect."""
        return await self._ctl("set", area,
                               json.dumps(updates, separators=(",", ":")))

    async def do_action(self, name: str) -> dict:
        """Does one thing that is not a setting."""
        return await self._ctl("action", name)

    async def _main(self):
        decky.logger.info("SteamOS Utility Center plugin loaded")

    async def _unload(self):
        decky.logger.info("SteamOS Utility Center plugin unloading")

    async def _uninstall(self):
        decky.logger.info("SteamOS Utility Center plugin uninstalled")
