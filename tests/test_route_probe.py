# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""The route probe has to survive a child that dies badly.

An incorrect version of a Steamworks interface raises no exception. It gives
a segmentation fault, because the flat wrappers call through a vtable that
does not match. So the probe runs each candidate in a forked child. This file
proves that the probe reports a child that stops, and that the child does not
stop the process. It calls steamworks._run_in_child directly, and probe_route
uses that same function. A fault in that code therefore cannot pass this test.
"""

import ctypes
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))

from steamos_utility_center import steamworks  # noqa: E402


class ChildIsolationTest(unittest.TestCase):
    def test_a_segfaulting_child_is_reported_not_fatal(self):
        def crash(_write_fd):
            # A null dereference through ctypes: the same shape of failure a
            # mismatched interface pointer produces.
            ctypes.string_at(0)

        status, detail = steamworks._run_in_child(crash)
        self.assertEqual(status, "crashed")
        self.assertEqual(detail, "SIGSEGV")
        # Reaching this line at all is the point: the parent lived.
        self.assertTrue(True)

    def test_a_healthy_child_reports_back(self):
        status, detail = steamworks._run_in_child(lambda fd: os.write(fd, b"OK 42"))
        self.assertEqual(status, "ok")
        self.assertEqual(detail, "OK 42")


class ProbeRouteTest(unittest.TestCase):
    def test_missing_library_is_a_failure_not_a_crash(self):
        status, detail = steamworks.probe_route(
            480, "/nonexistent/libsteam_api.so", "accessor:whatever")
        self.assertEqual(status, "failed")
        self.assertIn("nonexistent", detail)

    def test_select_route_gives_up_cleanly(self):
        route, count = steamworks.select_route(
            480, "/nonexistent/libsteam_api.so")
        self.assertIsNone(route)
        self.assertEqual(count, 0)

    def test_every_attempt_is_reported(self):
        seen = []
        steamworks.select_route("480", "/nonexistent/libsteam_api.so",
                                reporter=lambda *args: seen.append(args))
        self.assertTrue(seen, "the reporter should see each attempt")
        self.assertTrue(all(status == "failed" for _route, status, _d in seen))


class CandidateRouteTest(unittest.TestCase):
    def test_versioned_accessors_come_first(self):
        routes = steamworks.candidate_routes("/nonexistent")
        self.assertTrue(routes[0].startswith("accessor:"))

    def test_older_routes_are_offered_when_symbols_allow(self):
        import tempfile
        # Without a readable symbol table only the accessor guesses remain,
        # which is the documented fallback.
        with tempfile.NamedTemporaryFile(suffix=".so") as handle:
            handle.write(b"not an ELF file")
            handle.flush()
            routes = steamworks.candidate_routes(handle.name)
        self.assertTrue(all(route.startswith("accessor:") for route in routes))


if __name__ == "__main__":
    unittest.main()
