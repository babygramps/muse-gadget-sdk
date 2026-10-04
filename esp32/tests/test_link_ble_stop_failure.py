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

FAKES = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#define ESP_LOGI(tag, ...) ((void)(tag), (void)sizeof(printf(__VA_ARGS__)))
#define ESP_LOGD ESP_LOGI
#define ESP_LOGW ESP_LOGI
#define ESP_LOGE ESP_LOGI
#define pdMS_TO_TICKS(ms) (ms)
#define BLE_HS_CONN_HANDLE_NONE 0xffff
#define BLE_ERR_REM_USER_CONN_TERM 0x13
#define ESP_BT_MODE_BLE 1
#define ESP_OK 0
typedef int esp_err_t;
static const char *TAG = "test";
static uint16_t s_conn_handle = BLE_HS_CONN_HANDLE_NONE;
static bool s_subscribed, s_started = true, s_shutting_down, s_synced = true;
static bool s_advertising_enabled = true, s_advertising_active = true;
static int stop_result, stops, deinits, releases, restarts;
int ble_gap_adv_stop(void) { return 0; }
int ble_gap_terminate(uint16_t handle, uint8_t reason) { (void)handle; (void)reason; return 0; }
int nimble_port_stop(void) { stops++; return stop_result; }
esp_err_t nimble_port_deinit(void) { deinits++; return ESP_OK; }
esp_err_t esp_bt_mem_release(int mode) { (void)mode; releases++; return ESP_OK; }
const char *esp_err_to_name(esp_err_t err) { (void)err; return "ESP_FAIL"; }
void vTaskDelay(int ms) { (void)ms; }
void esp_restart(void) { restarts++; }
void stack_monitor_record(void *monitor) { (void)monitor; }
"""

MAIN = r"""
int main(void) {
    stop_result = -1;
    ble_server_full_shutdown();
    assert(stops == 3 && s_shutting_down && !s_advertising_enabled);
#if CONFIG_HOMEHUB_BLE_STOP_FAIL_NO_REBOOT
    // Logged; BLE stays stopped and nothing reboots.
    assert(restarts == 0 && deinits == 0 && releases == 0);
#else
    assert(restarts == 1);
#endif
    // Once shutting down, a second call does nothing.
    ble_server_full_shutdown();
    assert(stops == 3);

    // A clean stop releases the stack in either mode.
    s_shutting_down = false;
    s_started = true;
    stop_result = 0;
    stops = deinits = releases = restarts = 0;
    ble_server_full_shutdown();
    assert(stops == 1 && deinits == 1 && releases == 1 && restarts == 0 && !s_started);
    return 0;
}
"""


def function_source(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for end in range(brace, len(source)):
        if source[end] == "{":
            depth += 1
        elif source[end] == "}":
            depth -= 1
            if depth == 0:
                return source[start : end + 1]
    raise AssertionError(f"Unclosed function: {signature}")


class LinkBleStopFailureTest(unittest.TestCase):
    def run_shutdown(self, *flags: str) -> None:
        compiler = shlex.split(os.environ.get("CC", "cc"))
        if not compiler or shutil.which(compiler[0]) is None:
            self.skipTest("C compiler unavailable")
        shutdown = function_source((ROOT / "main/ble_server.c").read_text(),
                                   "void ble_server_full_shutdown(void) {")
        with tempfile.TemporaryDirectory() as name:
            tmp = Path(name)
            source = tmp / "shutdown.c"
            source.write_text(FAKES + shutdown + MAIN)
            binary = tmp / "shutdown"
            compiled = subprocess.run(
                [*compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", *flags,
                 str(source), "-o", str(binary)],
                capture_output=True, text=True,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_failed_stop_is_logged_without_rebooting_when_configured(self) -> None:
        self.run_shutdown("-DCONFIG_HOMEHUB_BLE_STOP_FAIL_NO_REBOOT=1")

    def test_failed_stop_reboots_by_default(self) -> None:
        self.run_shutdown()


if __name__ == "__main__":
    unittest.main()
