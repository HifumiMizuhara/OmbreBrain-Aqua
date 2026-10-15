"""dashboard.html 里 7000 多行内联 JS 至少得能被解析。

既有的前端契约测试全是字符串匹配（「这个函数在不在」「这个分支在不在」），
一个语法错误它们一条都不会红——页面照样发出去，然后整个 Dashboard 白屏。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

_HTML = Path(__file__).resolve().parent.parent / "frontend" / "dashboard.html"
_INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S)


def _blocks() -> list[str]:
    return _INLINE_SCRIPT.findall(_HTML.read_text(encoding="utf-8"))


@pytest.mark.skipif(shutil.which("node") is None, reason="node 不可用")
def test_inline_dashboard_scripts_parse():
    blocks = _blocks()
    assert blocks, "没找到内联 script，正则该跟着模板一起改"

    for index, source in enumerate(blocks):
        handle, path = tempfile.mkstemp(suffix=".js")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as f:
                f.write(source)
            result = subprocess.run(
                ["node", "--check", path], capture_output=True, text=True
            )
        finally:
            os.unlink(path)
        assert result.returncode == 0, (
            f"第 {index} 个内联 script 语法错误：\n{result.stderr}"
        )


@pytest.mark.skipif(shutil.which("node") is None, reason="node 不可用")
def test_concept_network_layout_convergence():
    """验证 _computeConceptLayout 在高密度网络下严格收敛，杜绝坐标发散飞出视口。"""
    html = _HTML.read_text(encoding="utf-8")
    match = re.search(r"function _computeConceptLayout[\s\S]*?\n\}", html)
    assert match, "没找到 _computeConceptLayout 函数实现"
    fn_code = match.group(0)

    test_js = f"""
let networkState = {{}};
{fn_code}

// 模拟高密度概念网络：30 个节点，随机大量连线（重现 5000+ 概念库下的 top 30 核心图）
const nodes = [];
for (let i = 0; i < 30; i++) {{
  nodes.push({{ id: "node_" + i, freq: (30 - i) * 3 }});
}}
const visible = new Set(nodes.map(n => n.id));
const visEdges = [];
for (let i = 0; i < 30; i++) {{
  for (let j = i + 1; j < 30; j++) {{
    if ((i + j) % 2 === 0 || (i * j) % 3 === 0) {{
      visEdges.push({{ source: nodes[i].id, target: nodes[j].id, weight: 1 + (i % 8) }});
    }}
  }}
}}
networkState = {{
  visibleNodes: visible,
  focusNode: null,
  positions: {{}},
  visEdges: visEdges
}};

_computeConceptLayout(800, 600, {{ nodes: nodes }});

for (let n of nodes) {{
  const p = networkState.positions[n.id];
  if (!p || !isFinite(p.x) || !isFinite(p.y)) {{
    throw new Error("坐标包含非法数值 NaN/Infinity: " + n.id);
  }}
  if (p.x < 40 || p.x > 760 || p.y < 40 || p.y > 560) {{
    throw new Error("坐标发散飞出视口 [800, 600]: " + n.id + " -> (" + p.x + ", " + p.y + ")");
  }}
}}

// 聚焦节点测试：focus 节点应锁定在中心
networkState.focusNode = "node_0";
networkState.positions = {{}};
_computeConceptLayout(800, 600, {{ nodes: nodes }});
const p0 = networkState.positions["node_0"];
if (Math.abs(p0.x - 400) > 1e-3 || Math.abs(p0.y - 300) > 1e-3) {{
  throw new Error("Focus 节点未正确锚定在中心: (" + p0.x + ", " + p0.y + ")");
}}
console.log("OK");
"""

    handle, path = tempfile.mkstemp(suffix=".js")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as f:
            f.write(test_js)
        result = subprocess.run(
            ["node", path], capture_output=True, text=True
        )
    finally:
        os.unlink(path)

    assert result.returncode == 0, f"力导向布局收敛性测试失败：\n{result.stderr}"
    assert "OK" in result.stdout

