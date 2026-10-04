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
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LinkNoiseCtrlCoreTest(unittest.TestCase):
    def test_session_task_is_pinned_only_when_asked_on_dual_core(self) -> None:
        noise = (ROOT / "main/noise_control.cpp").read_text()
        self.assertIn(
            "#if CONFIG_HOMEHUB_NOISE_CTRL_CORE >= 0 && !CONFIG_FREERTOS_UNICORE\n"
            "#define NOISE_CTRL_CORE CONFIG_HOMEHUB_NOISE_CTRL_CORE\n"
            "#else\n"
            "#define NOISE_CTRL_CORE tskNO_AFFINITY\n"
            "#endif\n",
            noise,
        )
        creates = re.findall(r"xTaskCreate\w*\(noise_ctrl_task,[^;]*;", noise)
        self.assertEqual(len(creates), 1)
        self.assertIn("xTaskCreatePinnedToCore(", creates[0])
        self.assertIn("NOISE_CTRL_CORE)", creates[0])

        kconfig = (ROOT / "main/Kconfig.projbuild").read_text()
        option = kconfig[kconfig.index("config HOMEHUB_NOISE_CTRL_CORE"):]
        self.assertRegex(option, r'int "[^"]*"\s+range -1 1\s+default -1')


if __name__ == "__main__":
    unittest.main()
