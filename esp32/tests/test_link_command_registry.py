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

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
NOISE_CPP = ROOT / "main" / "noise_control.cpp"

REGISTRY_HARNESS = r"""
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cJSON.h"
#include "command_registry.h"

static cJSON *handler(const char *command, cJSON *params, const char *request_id,
                      noise_ctrl_session_generation_t generation) {
    (void)command; (void)params; (void)request_id; (void)generation;
    return NULL;
}

#define DEF(name, params, timeout) { name, "Test command " name, params, timeout, handler }

int main(void) {
    static const noise_ctrl_command_def_t first[] = {
        DEF("smoker.status", NULL, 0),
        DEF("smoker.set_target",
            "{\"required\":{\"target_f\":{\"type\":\"integer\",\"description\":\"Pit target.\"}},"
            "\"optional\":{\"confirm\":{\"type\":\"boolean\"}}}", 15000),
    };
    assert(noise_ctrl_add_commands(first, 2));
    assert(command_registry_find("smoker.status") != NULL);
    assert(command_registry_find("smoker.set_target")->timeout_ms == 15000);
    assert(command_registry_find("smoker.nope") == NULL);
    assert(noise_ctrl_add_commands(first, 0));
    assert(noise_ctrl_add_commands(NULL, 0));

    // Each rejected batch adds nothing, not even its valid entries.
    const noise_ctrl_command_def_t sdk_name[] = {DEF("smoker.a", NULL, 0), DEF("device.health", NULL, 0)};
    const noise_ctrl_command_def_t twice[] = {DEF("smoker.b", NULL, 0), DEF("smoker.b", NULL, 0)};
    const noise_ctrl_command_def_t again[] = {DEF("smoker.status", NULL, 0)};
    const noise_ctrl_command_def_t bad_json[] = {DEF("smoker.c", "{\"required\":", 0)};
    const noise_ctrl_command_def_t bad_type[] = {DEF("smoker.c", "{\"required\":{\"x\":{\"type\":\"float\"}}}", 0)};
    const noise_ctrl_command_def_t trailing[] = {DEF("smoker.c", "{} garbage", 0)};
    const noise_ctrl_command_def_t bad_group[] = {DEF("smoker.c", "{\"extra\":{}}", 0)};
    const noise_ctrl_command_def_t bad_shape[] = {DEF("smoker.c", "{\"required\":[]}", 0)};
    const noise_ctrl_command_def_t bad_name[] = {DEF("smoker status", NULL, 0)};
    const noise_ctrl_command_def_t no_handler[] = {{"smoker.c", "Test", NULL, 0, NULL}};
    const noise_ctrl_command_def_t no_description[] = {{"smoker.c", "", NULL, 0, handler}};
    const noise_ctrl_command_def_t *rejected[] = {
        sdk_name, twice, again, bad_json, trailing, bad_type, bad_group, bad_shape, bad_name,
        no_handler, no_description,
    };
    const size_t sizes[] = {2, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1};
    for (size_t i = 0; i < sizeof(sizes) / sizeof(sizes[0]); i++) {
        assert(!noise_ctrl_add_commands(rejected[i], sizes[i]));
    }
    assert(!command_registry_find("smoker.a") && !command_registry_find("smoker.b"));
    assert(!command_registry_find("smoker.c"));

    // 24 in total: a batch that would pass the limit is refused whole.
    static char names[NOISE_CTRL_MAX_COMMANDS][16];
    static noise_ctrl_command_def_t more[NOISE_CTRL_MAX_COMMANDS];
    for (int i = 0; i < NOISE_CTRL_MAX_COMMANDS; i++) {
        snprintf(names[i], sizeof(names[i]), "smoker.n%d", i);
        noise_ctrl_command_def_t def = {names[i], "Filler", NULL, 0, handler};
        more[i] = def;
    }
    assert(!noise_ctrl_add_commands(more, NOISE_CTRL_MAX_COMMANDS - 1));
    assert(noise_ctrl_add_commands(more, NOISE_CTRL_MAX_COMMANDS - 2));
    assert(!noise_ctrl_add_commands(&more[NOISE_CTRL_MAX_COMMANDS - 2], 1));

    cJSON *commands = cJSON_CreateObject();
    command_registry_append(commands);
    char *json = cJSON_PrintUnformatted(commands);
    puts(json);
    free(json);
    cJSON_Delete(commands);
    return 0;
}
"""

QUEUE_FAKES = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include "cJSON.h"
typedef uint64_t noise_ctrl_session_generation_t;
static int queued;
static const char *last_code;
static void queue_result(noise_ctrl_session_generation_t generation, const char *request_id,
                         cJSON *result) {
    assert(generation == 7 && strcmp(request_id, "req") == 0);
    cJSON *error = cJSON_GetObjectItem(result, "error");
    cJSON *code = error ? cJSON_GetObjectItem(error, "code") : NULL;
    last_code = code ? "handler_failed" : "ok";
    queued++;
    cJSON_Delete(result);
}
"""

QUEUE_MAIN = r"""
int main(void) {
    cJSON *ok = cJSON_CreateObject();
    cJSON_AddBoolToObject(ok, "ok", 1);
    queue_handler_result(7, "req", ok);
    assert(queued == 1 && strcmp(last_code, "ok") == 0);
    cJSON *later = cJSON_CreateObject();
    cJSON_AddBoolToObject(later, "_async", 1);
    queue_handler_result(7, "req", later);
    assert(queued == 1);
    queue_handler_result(7, "req", NULL);
    assert(queued == 2 && strcmp(last_code, "handler_failed") == 0);
    return 0;
}
"""

REGISTER_PREAMBLE = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cJSON.h"
#include "command_registry.h"
#include "esp_app_desc.h"
#define nullptr NULL
#define CONFIG_HOMEHUB_DEVICE_FAMILY "homehub"
#define CONFIG_HOMEHUB_MODEL_ID "smoker"
bool ota_is_enabled(void) { return false; }
static esp_app_desc_t app = { .version = "1.0.0" };
const esp_app_desc_t *esp_app_get_description(void) { return &app; }
static char s_wifi_ssid[33] = "SmokerNet", s_register_req_id[40];
static const char *s_node_id = "homelink-000000", *s_display_name = "Smoker";
static void copy_wifi_ssid(char *out, size_t size) { snprintf(out, size, "%s", s_wifi_ssid); }
static void make_uuid(char *out, size_t size) { snprintf(out, size, "test-request"); }
"""

REGISTER_MAIN = r"""
static cJSON *handler(const char *command, cJSON *params, const char *request_id,
                      noise_ctrl_session_generation_t generation) {
    (void)command; (void)params; (void)request_id; (void)generation;
    return NULL;
}
int main(void) {
    // A full table with long descriptions: well past the old 8 KB limit.
    static char names[NOISE_CTRL_MAX_COMMANDS][24], text[480];
    static noise_ctrl_command_def_t defs[NOISE_CTRL_MAX_COMMANDS];
    memset(text, 'd', sizeof(text) - 1);
    for (int i = 0; i < NOISE_CTRL_MAX_COMMANDS; i++) {
        snprintf(names[i], sizeof(names[i]), "smoker.command_%02d", i);
        noise_ctrl_command_def_t def = {
            names[i], text,
            "{\"required\":{\"value\":{\"type\":\"integer\",\"description\":\"A value.\"}}}",
            20000, handler,
        };
        defs[i] = def;
    }
    assert(noise_ctrl_add_commands(defs, NOISE_CTRL_MAX_COMMANDS));
    char *json = build_register_json();
    assert(json);
    puts(json);
    free(json);
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


class LinkCommandRegistryTest(unittest.TestCase):
    def compile_and_run(self, sources: list[Path], tmp: Path) -> str:
        compiler = shlex.split(os.environ.get("CC", "cc"))
        if not compiler or shutil.which(compiler[0]) is None:
            self.skipTest("C compiler unavailable")
        binary = tmp / "registry"
        compiled = subprocess.run(
            [*compiler, "-std=c11", "-D_GNU_SOURCE", "-Wall", "-Wextra", "-Werror",
             "-I", str(ROOT / "tests/link_fakes"), "-I", str(ROOT / "main"),
             *map(str, sources), "-o", str(binary)],
            capture_output=True, text=True,
        )
        self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
        ran = subprocess.run([str(binary)], capture_output=True, text=True)
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        return ran.stdout

    def test_registry_validates_whole_batches(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            tmp = Path(name)
            (tmp / "harness.c").write_text(REGISTRY_HARNESS)
            output = self.compile_and_run(
                [tmp / "harness.c", ROOT / "main/command_registry.c",
                 ROOT / "tests/link_fakes/cJSON.c"], tmp,
            )
        commands = json.loads(output)
        self.assertEqual(len(commands), 24)
        self.assertEqual(commands["smoker.status"],
                         {"required": {}, "optional": {}, "description": "Test command smoker.status"})
        target = commands["smoker.set_target"]
        self.assertEqual(target["required"]["target_f"]["type"], "integer")
        self.assertEqual(target["optional"]["confirm"]["type"], "boolean")
        self.assertEqual(target["timeout_ms"], 15000)

    def test_full_table_fits_the_register_buffer(self) -> None:
        noise = NOISE_CPP.read_text()
        functions = "\n".join(function_source(noise, signature) for signature in (
            "static cJSON *string_param(",
            "static void add_command(",
            "static void add_register_metadata_string(",
            "static char *build_register_json(",
        ))
        with tempfile.TemporaryDirectory() as name:
            tmp = Path(name)
            source = tmp / "register.c"
            source.write_text(REGISTER_PREAMBLE + functions + REGISTER_MAIN)
            output = self.compile_and_run(
                [source, ROOT / "main/command_registry.c", ROOT / "tests/link_fakes/cJSON.c"], tmp,
            )
        self.assertGreater(len(output), 12000)
        params = json.loads(output)["params"]
        self.assertEqual((params["device_family"], params["model_id"]), ("homehub", "smoker"))
        self.assertIn("device.health", params["commands_v2"])
        self.assertEqual(params["commands_v2"]["smoker.command_23"]["timeout_ms"], 20000)

    def test_registered_handlers_come_before_the_fallback(self) -> None:
        noise = NOISE_CPP.read_text()
        invoke = function_source(noise, "static void handle_invoke_request(")
        health = invoke.index('strcmp(command, "device.health") == 0')
        registry = invoke.index("command_registry_find(command)")
        fallback = invoke.index("s_command_cb(command, cmd_params")
        self.assertLess(health, registry)
        self.assertLess(registry, fallback)
        self.assertEqual(invoke.count("queue_handler_result("), 2)

        helper = function_source(noise, "static void queue_handler_result(")
        with tempfile.TemporaryDirectory() as name:
            tmp = Path(name)
            source = tmp / "queue.c"
            source.write_text(QUEUE_FAKES + helper + QUEUE_MAIN)
            self.compile_and_run([source, ROOT / "tests/link_fakes/cJSON.c"], tmp)

        cmake = (ROOT / "main/CMakeLists.txt").read_text()
        self.assertIn('"command_registry.c"', cmake)


if __name__ == "__main__":
    unittest.main()
