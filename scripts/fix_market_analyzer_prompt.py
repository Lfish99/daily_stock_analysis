"""One-shot script to restore the EN prompt block in market_analyzer.py."""
import pathlib

p = pathlib.Path("src/market_analyzer.py")
content = p.read_text(encoding="utf-8")

# Find the exact anchor text to work with
anchor = "{data_no_indices_hint}\n\n### 3. Fund Flows"
target = "{self._get_index_hint()})"

if anchor not in content:
    print("anchor not found — check file")
    exit(1)

if target in content:
    print("target already present — nothing to do")
    exit(0)

report_title_block = (
    "\n{self._get_strategy_prompt_block()}\n"
    "\n---\n"
    "\n# Output Template (follow this structure)\n"
    "\n## {report_title}\n"
    "\n### 1. Market Summary\n"
    "(2-3 sentences summarizing overall market tone, index moves, and liquidity.)\n"
    "\n### 2. Index Commentary\n"
    "({self._get_index_hint()})\n"
    "\n### 3. Fund Flows"
)

content = content.replace(
    "{data_no_indices_hint}\n\n### 3. Fund Flows",
    "{data_no_indices_hint}" + report_title_block,
    1,
)
p.write_text(content, encoding="utf-8")
print("DONE — EN prompt restored")

