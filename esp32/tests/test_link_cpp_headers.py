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
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]

# Defined in C, as the SDK defines them (gadget_pre_app_hook stands in for the
# project's own definition, which C++ code must give C linkage too).
C_SIDE = r"""
#include <stdbool.h>
#include <stddef.h>
const char *identity_node_id(void) { return "homelink-000000"; }
bool wifi_mgr_is_connected(void) { return true; }
void app_run(void) {}
void gadget_pre_app_hook(void) {}
"""

CPP_SIDE = r"""
#include "app.h"
#include "gadget_hooks.h"
#include "identity.h"
#include "wifi_mgr.h"
int main() {
    app_run();
    gadget_pre_app_hook();
    return identity_node_id()[0] == 'h' && wifi_mgr_is_connected() ? 0 : 1;
}
"""


class LinkCppHeadersTest(unittest.TestCase):
    def test_project_facing_headers_link_from_cpp(self) -> None:
        cc = shlex.split(os.environ.get("CC", "cc"))
        cxx = shlex.split(os.environ.get("CXX", "c++"))
        if not cc or shutil.which(cc[0]) is None or not cxx or shutil.which(cxx[0]) is None:
            self.skipTest("C and C++ compilers unavailable")
        with tempfile.TemporaryDirectory() as name:
            tmp = Path(name)
            (tmp / "c_side.c").write_text(C_SIDE)
            (tmp / "cpp_side.cpp").write_text(CPP_SIDE)
            includes = ["-I", str(ROOT / "tests/link_fakes"), "-I", str(ROOT / "main")]
            steps = [
                [*cc, "-std=c11", "-c", str(tmp / "c_side.c"), "-o", str(tmp / "c_side.o")],
                [*cxx, "-std=c++17", "-Wall", "-Wextra", "-Werror", *includes,
                 str(tmp / "cpp_side.cpp"), str(tmp / "c_side.o"), "-o", str(tmp / "linked")],
                [str(tmp / "linked")],
            ]
            for step in steps:
                ran = subprocess.run(step, capture_output=True, text=True)
                self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)


if __name__ == "__main__":
    unittest.main()
