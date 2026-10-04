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

HARNESS = r"""
#include <assert.h>
#include <stdbool.h>
#include <string.h>
#include "sdkconfig.h"

static char order[4];
static int calls;
void app_run(void) { order[calls++] = 'r'; }
#if CONFIG_GADGET_PRE_APP_HOOK
void gadget_pre_app_hook(void) { order[calls++] = 'h'; }
#endif
void app_main(void);

int main(void) {
    app_main();
#if CONFIG_GADGET_PRE_APP_HOOK
    assert(strcmp(order, "hr") == 0);
#else
    assert(strcmp(order, "r") == 0);
#endif
    return 0;
}
"""


class LinkPreAppHookTest(unittest.TestCase):
    def run_app_main(self, hook: bool) -> None:
        compiler = shlex.split(os.environ.get("CC", "cc"))
        if not compiler or shutil.which(compiler[0]) is None:
            self.skipTest("C compiler unavailable")
        with tempfile.TemporaryDirectory() as name:
            tmp = Path(name)
            (tmp / "sdkconfig.h").write_text(
                '#define CONFIG_GADGET_PRODUCT_NAME "Test gadget"\n'
                + ("#define CONFIG_GADGET_PRE_APP_HOOK 1\n" if hook else "")
            )
            (tmp / "esp_log.h").write_text(
                '#include "sdkconfig.h"\n#include <stdio.h>\n'
                "#define ESP_LOGI(tag, ...) ((void)(tag), (void)sizeof(printf(__VA_ARGS__)))\n"
                "#define ESP_LOGW ESP_LOGI\n"
            )
            (tmp / "harness.c").write_text(HARNESS)
            binary = tmp / "pre-app-hook"
            compiled = subprocess.run(
                [*compiler, "-std=c11", "-Wall", "-Wextra", "-Werror",
                 "-I", str(tmp), "-I", str(ROOT / "main"),
                 str(ROOT / "main/main.c"), str(tmp / "harness.c"), "-o", str(binary)],
                capture_output=True, text=True,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_hook_runs_before_app_run_when_enabled(self) -> None:
        self.run_app_main(hook=True)

    def test_default_build_needs_no_hook(self) -> None:
        self.run_app_main(hook=False)


if __name__ == "__main__":
    unittest.main()
