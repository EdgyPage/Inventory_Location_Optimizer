---
name: no-triple-single-quotes-in-bash-heredocs
description: "A Bash-tool command whose heredoc body contains ''' (or escaped \\\") dies with \"unexpected EOF while looking for matching `''\" before anything runs; write patch scripts to a file with the Write tool and run them instead"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 0719e7f3-107b-4218-9c81-fdfe0ef82a55
  modified: 2026-09-06T03:58:09.612Z
---

Inline `python - <<'PYEOF' ... PYEOF` commands through the Bash tool fail at the SHELL with
"unexpected EOF while looking for matching `''" whenever the heredoc body contains a Python
triple-single-quote (`'''`) or backslash-escaped double quotes (`\"\"\"`), even though the
delimiter is quoted. Bodies containing only `"""` run fine. Hit three times in one session
(sim_config, Picking_Data and strategy_runner patch scripts, 2026-09-05).

**Why:** the tool's command wrapper re-parses the heredoc text; quoted delimiters do not
protect `'''` from it. Nothing runs, so no partial edit lands, but each failure costs a full
re-issue of a multi-KB script.

**How to apply:** for any multi-line Python patch, `Write` it to the scratchpad as a `.py`
file and run `python <file>`. Keep heredoc bodies to `"""`-only strings when a one-liner is
unavoidable. Related: [[no-unicode-escapes-in-heredoc-python]].
