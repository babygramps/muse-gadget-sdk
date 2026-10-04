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
NOISE_CPP = ROOT / "main" / "noise_control.cpp"


class LinkDeviceRequestsContractTest(unittest.TestCase):
    def test_request_channel_builds_with_muse_or_on_request(self) -> None:
        source = NOISE_CPP.read_text()
        self.assertIn(
            "#define NOISE_CTRL_DEVICE_REQUESTS "
            "(CONFIG_MUSE_ENABLED || CONFIG_HOMEHUB_DEVICE_REQUESTS)",
            source,
        )
        gates = [m.start() for m in re.finditer(r"#if NOISE_CTRL_DEVICE_REQUESTS\n", source)]
        self.assertEqual(len(gates), 3)
        # The queue and frame handling, the session's transmit pump, and the
        # public noise_ctrl_req_* functions all sit behind the same gate.
        for marker in ("#define REQ_STREAM_BASE 16",
                       "if (!req_pump_tx(",
                       'extern "C" int64_t noise_ctrl_req_open('):
            position = source.index(marker)
            gate = max(g for g in gates if g < position)
            between = source[gate:position]
            self.assertNotIn("#endif", between, marker)
        public = source[gates[2]:source.rindex("#endif  // NOISE_CTRL_DEVICE_REQUESTS")]
        self.assertIn('extern "C" bool noise_ctrl_req_send(', public)
        self.assertIn('extern "C" void noise_ctrl_req_cancel(', public)
        self.assertNotIn("#endif  // CONFIG_MUSE_ENABLED", source)

        # The request pump's DMA check lives with the tunnel's.
        tunnel = (ROOT / "main/noise_tunnel.cpp").read_text()
        self.assertRegex(
            tunnel,
            r"#if CONFIG_MUSE_ENABLED \|\| CONFIG_HOMEHUB_DEVICE_REQUESTS\n"
            r'extern "C" bool noise_tx_has_dma_headroom_reclaiming\(',
        )

        kconfig = (ROOT / "main/Kconfig.projbuild").read_text()
        option = kconfig[kconfig.index("config HOMEHUB_DEVICE_REQUESTS"):]
        self.assertRegex(option, r'bool "[^"]*"\s+default n')


if __name__ == "__main__":
    unittest.main()
