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
APP_C = ROOT / "main" / "app.c"

FAKES = r"""
#include <assert.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "led_status.h"
#define ESP_LOGI(tag, ...) ((void)(tag), (void)sizeof(printf(__VA_ARGS__)))
static const char *TAG = "test";
enum { SCAN_REFRESH_REPLY_PENDING = 2 };
static atomic_uint s_scan_refresh_flags;
static atomic_bool s_setup_advertising;
#if CONFIG_HOMEHUB_SETUP_WINDOW_S > 0
static atomic_uint s_setup_window_closes_s;
#endif
static int64_t now_us;
static bool setup_done, started, connected, ap;
static int begins, stops;
static const char *status_ui = "";
static led_state_t led;
int64_t esp_timer_get_time(void) { return now_us; }
bool config_setup_complete(void) { return setup_done; }
bool ble_server_is_started(void) { return started; }
bool ble_server_has_connection(void) { return connected; }
void ble_server_begin_advertising(void) { begins++; }
void ble_server_stop_advertising(bool disconnect_client) { assert(!disconnect_client); stops++; }
void start_ble_setup_server_if_needed(void) { started = true; }
bool wifi_mgr_ap_active(void) { return ap; }
void setup_stage_set(const char *stage) { (void)stage; }
void ui_set_ble(const char *s) { (void)s; }
void ui_set_status(const char *s) { status_ui = s; }
void led_status_set_state(led_state_t state) { led = state; }
void setup_window_lock_take(void) {}
void setup_window_lock_give(void) {}
uint32_t advance_confirm_generation_locked(void) { return 1; }
static void at(int seconds) { now_us = (int64_t)seconds * 1000000; }
"""

WINDOW_MAIN = r"""
int main(void) {
    // Boot opens a 600 s window.
    at(1);
    open_setup_window("boot: unpaired");
    assert(begins == 1 && stops == 0 && atomic_load(&s_setup_advertising));
    assert(led == LED_STATE_BLE_ADVERTISING);
    at(600);
    update_setup_advertising();
    assert(stops == 0);
    at(601);
    update_setup_advertising();
    assert(stops == 1 && !atomic_load(&s_setup_advertising));
    assert(led == LED_STATE_SETUP_IDLE && strcmp(status_ui, "pairing_closed") == 0);
    update_setup_advertising();
    assert(stops == 1 && begins == 1);

    // A short press opens it again; the SoftAP pauses and resumes it.
    at(700);
    open_setup_window("button short-press");
    assert(begins == 2);
    ap = true;
    at(710);
    update_setup_advertising();
    assert(stops == 2);
    ap = false;
    at(720);
    update_setup_advertising();
    assert(begins == 3 && atomic_load(&s_setup_advertising));

    // A phone mid-setup keeps its session past the deadline; when it leaves,
    // advertising does not come back.
    connected = true;
    at(1400);
    update_setup_advertising();
    assert(stops == 2);
    connected = false;
    on_client_disconnected();
    assert(stops == 3 && begins == 3);

    // Inside the window a departing phone brings advertising back.
    at(2000);
    open_setup_window("button short-press");
    on_client_disconnected();
    assert(begins == 5);

    // Opening while the SoftAP is up advertises nothing.
    ap = true;
    open_setup_window("button short-press");
    assert(begins == 5 && stops == 4);
    ap = false;

    // Paired: nothing changes.
    setup_done = true;
    at(9000);
    update_setup_advertising();
    open_setup_window("button short-press");
    assert(begins == 5 && stops == 4);
    return 0;
}
"""

UPSTREAM_MAIN = r"""
int main(void) {
    at(1);
    open_setup_window("boot: unpaired");
    at(100000);
    on_client_disconnected();
    assert(begins == 2 && stops == 0 && atomic_load(&s_setup_advertising));
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


class LinkSetupWindowTest(unittest.TestCase):
    def run_window(self, main: str, *flags: str, managed: bool) -> None:
        compiler = shlex.split(os.environ.get("CC", "cc"))
        if not compiler or shutil.which(compiler[0]) is None:
            self.skipTest("C compiler unavailable")
        app = APP_C.read_text()
        signatures = [
            "static bool setup_advertising_blocked(void) {",
            "static void pause_setup_advertising(const char *reason) {",
            "static void enter_advertising_state(const char *reason) {",
            "static void open_setup_window(const char *reason) {",
            "static void on_client_disconnected(void) {",
        ]
        if managed:
            signatures.insert(0, "static uint32_t uptime_s(void) {")
            signatures.append("static void update_setup_advertising(void) {")
        functions = "\n".join(function_source(app, s) for s in signatures)
        with tempfile.TemporaryDirectory() as name:
            tmp = Path(name)
            source = tmp / "window.c"
            source.write_text(FAKES + functions + main)
            binary = tmp / "window"
            compiled = subprocess.run(
                [*compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", *flags,
                 "-I", str(ROOT / "main"), str(source), "-o", str(binary)],
                capture_output=True, text=True,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_window_closes_and_softap_pauses_advertising(self) -> None:
        self.run_window(WINDOW_MAIN, "-DCONFIG_HOMEHUB_SETUP_WINDOW_S=600",
                        "-DCONFIG_HOMEHUB_WIFI_EXTERNAL=1", managed=True)

    def test_default_advertises_for_as_long_as_unpaired(self) -> None:
        self.run_window(UPSTREAM_MAIN, "-DCONFIG_HOMEHUB_SETUP_WINDOW_S=0", managed=False)

    def test_heartbeat_drives_the_window(self) -> None:
        app_run = function_source(APP_C.read_text(), "void app_run(")
        self.assertRegex(
            app_run,
            r"#if CONFIG_HOMEHUB_SETUP_WINDOW_S > 0 \|\| CONFIG_HOMEHUB_WIFI_EXTERNAL\s+"
            r"update_setup_advertising\(\);\s+#endif",
        )
        kconfig = (ROOT / "main/Kconfig.projbuild").read_text()
        option = kconfig[kconfig.index("config HOMEHUB_SETUP_WINDOW_S"):]
        self.assertRegex(option, r'int "[^"]*"\s+range 0 86400\s+default 0')


if __name__ == "__main__":
    unittest.main()
