# Experience bank: build it once with the person

The experience bank (`profile/experience.yaml` in the data repo) is the single source of facts about the person.
Snoopy judges fit from it, and `radar.cv` builds tailored CVs from it. Format and a fictional example:
`config.example/experience.yaml`. Run this with the person present (it's an interview), never from a scheduled run.

## Ground rules

- **Only what the person tells you.** Never invent a number, a title, a date or an outcome. If a result has no
  number, ask once ("roughly how much time, money or how many people?"); if they don't know, keep it qualitative.
- **STAR for every achievement:** situation (context, one line), task (their responsibility), action (what they
  did), result (what changed, measured where possible). Ask for the parts that are missing; don't fill them.
- Short and concrete beats long: one line per STAR part.
- Their words for their work. Tidy grammar, never upgrade claims ("helped with" stays "helped with").

## Two ways in

**A. They have a CV (fastest).** Ask them to share it (PDF, Word or pasted text). Draft the bank from it: one
role per job, one achievement per bullet. Then walk through the gaps with them, role by role: a CV bullet is
usually an action without a situation, or a result without a number. Read each draft back and let them correct
it before saving.

**B. From scratch.** Newest role first. For each role ask:
1. Company, title, dates, location, and one line about the company (size, stage, what it does).
2. "What are the two or three things from this job you'd most want an interviewer to ask about?"
3. For each: what was going on (situation), what you were responsible for (task), what you did (action), what
   changed and by how much (result).
4. Which skills and tools that used, and which kinds of roles it's evidence for (tags such as operations,
   product, sales, strategy, engineering, the industry).
Then education, skills, languages, and two or three one-line summaries for different kinds of roles.

Aim for 3 to 5 achievements per recent role and 1 to 2 for older ones. 30 to 45 minutes is typical; it can be
done over several sessions (save as you go).

## Saving

Write `profile/experience.yaml` in the data repo, validate it
(`PYTHONPATH=<framework> python -m radar.cv --data <data repo> --check`), commit and push. Then regenerate
`profile/brief.md` (the judging brief) from the bank: education, roles newest first with one line each,
skills, languages, total years, constraints. Tell the person they can update the bank any time by asking.

## Tailored CVs (on request only)

When the person asks for a CV for a role in the report:
1. `PYTHONPATH=<framework> python -m radar.cv --data <data repo> --role <role id or URL>` picks the most
   relevant achievements by matching the job description against each achievement's text, skills and tags,
   and writes `cvs/<company>-<title>.html` (and `.pdf` when the browser is installed).
2. Optionally refine: write `cvs/<slug>.plan.json` with `{"summary": "<summary id>", "achievements": ["<id>", ...],
   "rephrase": {"<id>": "<one-line bullet>"}}` and run the command again with `--plan`. Rephrasing may reorder
   and shorten, never add facts; keep every number exactly as in the bank.
3. Show the person the result. Never send it anywhere, and never edit CVs the person made themselves.
