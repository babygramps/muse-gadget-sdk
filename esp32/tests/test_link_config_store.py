#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LinkConfigStoreHarnessTest(unittest.TestCase):
    def test_config_store_failure_semantics(self) -> None:
        cc = shlex.split(os.environ.get("CC", "cc"))
        if not cc or shutil.which(cc[0]) is None:
            raise unittest.SkipTest("C compiler not available")

        for flags in (
            ["-DLINK_FAKE_ERROR_CHECK_HOOK=1"],
            ["-DLINK_FAKE_ERROR_CHECK_HOOK=1", "-DCONFIG_HOMEHUB_NVS_FAIL_DISABLES_LINK=1"],
        ):
            with self.subTest(flags=flags), tempfile.TemporaryDirectory() as tmpdir:
                binary = Path(tmpdir) / "link_config_store_harness"
                compile_proc = subprocess.run(
                    [
                        *cc,
                        "-std=c11",
                        "-Wall",
                        "-Wextra",
                        *flags,
                        "-I",
                        str(ROOT / "tests" / "link_fakes"),
                        "-I",
                        str(ROOT / "main"),
                        str(ROOT / "tests" / "link_config_store_harness.c"),
                        str(ROOT / "main" / "config_store.c"),
                        "-o",
                        str(binary),
                    ],
                    cwd=ROOT,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.assertEqual(
                    compile_proc.returncode,
                    0,
                    msg=compile_proc.stdout + compile_proc.stderr,
                )

                run_proc = subprocess.run(
                    [str(binary)],
                    cwd=ROOT,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.assertEqual(
                    run_proc.returncode,
                    0,
                    msg=run_proc.stdout + run_proc.stderr,
                )

    def test_app_run_returns_when_storage_is_unavailable(self) -> None:
        app = (ROOT / "main" / "app.c").read_text()
        body = app[app.index("void app_run(void) {"):]
        guard = body.index("if (!config_store_init()) {")
        self.assertLess(guard, body.index("wifi_known_init();"))
        self.assertIn("return;", body[guard:body.index("wifi_known_init();")])
        self.assertIn("atomic_store(&s_runtime_disabled, true);", body[guard:body.index("wifi_known_init();")])

    def test_public_entry_points_are_inert_when_the_runtime_is_disabled(self) -> None:
        app = (ROOT / "main" / "app.c").read_text()
        entry_points = [
            "void app_wifi_set_credentials(",
            "bool app_wifi_forget(",
            "app_wifi_join_t app_wifi_join_saved(",
            "bool app_wifi_nap(",
            "void app_ble_companion_set(",
            "bool app_confirm_pairing_press(",
            "void app_reset_setup_async(",
            "void app_open_setup_window(",
            "void app_request_unpair(",
            "void app_get_gadget_status(",
        ]
        for signature in entry_points:
            with self.subTest(entry_point=signature):
                start = app.index(signature)
                opening = app[app.index("{", start):][:200]
                self.assertIn("if (runtime_disabled())", opening)

if __name__ == "__main__":
    unittest.main()
