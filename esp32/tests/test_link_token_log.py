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
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]

# Fake 48-character token: "mgst_" plus 43 characters, none of which repeat the prefix.
TOKEN = "mgst_" + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopq"[:43]
assert len(TOKEN) == 48

# The three places the SDK logs a token prefix, and a marker unique to each line.
SITES = {
    "main/app.c": "SDK token:",
    "main/link_pairing.c": "pairing_confirmed carries SDK token",
    "main/vm_api.c": "refresh carries SDK token",
}

# The lines under test are cut out of the real sources, so a harness that passes can
# only mean the shipped log statements behave. Each prints through ESP_LOGI to stdout.
HARNESS = r"""
#include <stdio.h>
#include <string.h>
#include "sdkconfig.h"
#include "sdk_token_log.h"

#define ESP_LOGI(tag, ...) (printf("%s: ", tag), printf(__VA_ARGS__), printf("\n"))
static const char *TAG = "test";
static const char *s_token;
const char *identity_sdk_token(void) { return s_token; }

int main(void) {
    static char token_copy[64];
    snprintf(token_copy, sizeof(token_copy), "%s", TOKEN);
    s_token = token_copy;
    BANNER
    SITES
    s_token = NULL;
    BANNER
    return 0;
}
"""


def log_statement(relative: str, marker: str) -> str:
    source = (ROOT / relative).read_text()
    found = [m.group(0) for m in re.finditer(r"ESP_LOGI\(TAG,[^;]*;", source)
             if marker in m.group(0)]
    assert len(found) == 1, f"{relative}: expected one log line with {marker!r}, got {found}"
    return found[0]


class LinkTokenLogTest(unittest.TestCase):
    def run_sites(self, prefix_option: bool) -> str:
        compiler = shlex.split(os.environ.get("CC", "cc"))
        if not compiler or shutil.which(compiler[0]) is None:
            self.skipTest("C compiler unavailable")
        statements = {path: log_statement(path, marker) for path, marker in SITES.items()}
        # The pairing and refresh sites read their own static copy of the token;
        # the harness aliases both names to the one fake.
        sites = "\n    ".join(statements[p].replace("s_sdk_token", "s_token")
                              for p in ("main/link_pairing.c", "main/vm_api.c"))
        banner = statements["main/app.c"]
        with tempfile.TemporaryDirectory() as name:
            tmp = Path(name)
            # sdkconfig.h defines a bool option only when it is y.
            (tmp / "sdkconfig.h").write_text(
                "#define CONFIG_HOMEHUB_LOG_SDK_TOKEN_PREFIX 1\n" if prefix_option else "")
            source = tmp / "harness.c"
            source.write_text(HARNESS.replace("TOKEN", f'"{TOKEN}"')
                              .replace("SITES", sites).replace("BANNER", banner))
            binary = tmp / "harness"
            compiled = subprocess.run(
                [*compiler, "-std=c11", "-Wall", "-Wextra", "-Werror",
                 "-I", str(tmp), "-I", str(ROOT / "main"), str(source), "-o", str(binary)],
                capture_output=True, text=True,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            return ran.stdout

    def test_option_on_logs_the_prefix_exactly_as_upstream(self) -> None:
        output = self.run_sites(True)
        self.assertNotIn(TOKEN, output)
        self.assertEqual(output.splitlines(), [
            f"test:   SDK token:  {TOKEN[:12]}",
            f"test: pairing_confirmed carries SDK token {TOKEN[:12]}",
            f"test: refresh carries SDK token {TOKEN[:12]}",
            "test:   SDK token:  (none)",
        ])

    def test_option_off_logs_no_character_of_the_token(self) -> None:
        output = self.run_sites(False)
        self.assertNotIn(TOKEN[5:], output)
        self.assertNotIn(TOKEN[0:12], output)
        self.assertNotIn("mgst_", output)
        self.assertEqual(output.splitlines(), [
            "test:   SDK token:  set",
            "test: pairing_confirmed carries SDK token set",
            "test: refresh carries SDK token set",
            "test:   SDK token:  (none)",
        ])

    def test_default_build_keeps_the_prefix(self) -> None:
        kconfig = (ROOT / "main/Kconfig.projbuild").read_text()
        block = kconfig[kconfig.index("config HOMEHUB_LOG_SDK_TOKEN_PREFIX"):]
        block = block.split("\n    config ", 1)[0].split("\nendmenu", 1)[0]
        self.assertIn("bool", block)
        self.assertIn("default y", block)
        # V1 regenerates this file from the board defaults when it exists.
        generated = ROOT / "build-reterminal-e1001/sdkconfig"
        if generated.exists():
            self.assertIn("CONFIG_HOMEHUB_LOG_SDK_TOKEN_PREFIX=y", generated.read_text())

    def test_no_site_formats_the_raw_token(self) -> None:
        for path, marker in SITES.items():
            line = log_statement(path, marker)
            self.assertIn("sdk_token_log_text(", line, path)


if __name__ == "__main__":
    unittest.main()
