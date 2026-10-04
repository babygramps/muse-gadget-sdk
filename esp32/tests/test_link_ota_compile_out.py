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

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LinkOtaCompileOutTest(unittest.TestCase):
    def test_option_removes_every_ota_entry_point(self) -> None:
        noise = (ROOT / "main/noise_control.cpp").read_text()
        ble = (ROOT / "main/ble_server.c").read_text()
        for source, macro in ((noise, "OTA_INVOKE"), (ble, "BLE_OTA_ACTIONS")):
            self.assertIn(
                f"#if CONFIG_HOMEHUB_OTA_COMPILE_OUT\n#define {macro} 0\n#else\n"
                f"#define {macro} 1\n#endif\n",
                source,
            )
        # The invoke falls through to "unsupported command"; the BLE action to
        # error_unknown_action; provisioning never copies ota_url.
        self.assertIn('if (OTA_INVOKE && strcmp(command, "device.ota") == 0) {', noise)
        self.assertIn(
            '} else if (decrypted && strcmp(act, "device.ota") == 0 && BLE_OTA_ACTIONS) {', ble
        )
        self.assertIn("if (BLE_OTA_ACTIONS && *ota_url) {", ble)
        registry = (ROOT / "main/command_registry.c").read_text()
        self.assertIn('"device.ota",', registry)

        kconfig = (ROOT / "main/Kconfig.projbuild").read_text()
        option = kconfig[kconfig.index("config HOMEHUB_OTA_COMPILE_OUT"):]
        self.assertRegex(option, r'bool "[^"]*"\s+depends on !HOMEHUB_OTA_ENABLED\s+default n')


if __name__ == "__main__":
    unittest.main()
