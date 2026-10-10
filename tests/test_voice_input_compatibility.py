"""Exercise the shipped custom input handler's IME boundary without a microphone."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_starmap_composing_enter_never_submits_or_clears_text():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the browser input handler check")
    html = Path(__file__).parents[1] / "src/components/knowledge_starmap/index.html"
    script = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const html = fs.readFileSync(process.argv[1], 'utf8');
const start = html.indexOf("elInput.addEventListener('keydown', function (e) {");
const end = html.indexOf('});', start) + 3;
const input = {value: '我当时的依据。日本語。English.',
    addEventListener: (_event, callback) => { input.handle = callback; }};
const calls = [];
vm.runInNewContext(html.slice(start, end), {
    elInput: input, show: () => {}, dispatch: (value) => calls.push(value)
});
const original = input.value;
for (const event of [{key:'Enter', isComposing:true}, {key:'Enter', keyCode:229}]) {
    input.handle(event);
    assert.equal(input.value, original);
    assert.equal(calls.length, 0);
}
input.handle({key:'Enter', isComposing:false, keyCode:13});
assert.deepEqual(calls, [original]);
assert.equal(input.value, '');
"""
    result = subprocess.run([node, "-e", script, str(html)], capture_output=True, text=True,
                            timeout=20, check=False)
    assert result.returncode == 0, result.stderr
