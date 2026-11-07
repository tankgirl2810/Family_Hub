---
name: humanize-code
description: Refactor Python and SQL (Databricks, SSMS) so it's tighter and reads like I wrote it, in Australian English with my commenting habits. Use when asked to humanise, clean up, tighten, or make code look like mine.
---

# Humanise My Code

When this skill is called, rework the code so it looks like I wrote it myself. That means tighter than a textbook version, a bit messy in the ways I'm actually messy, and commented the way I comment. It should not look like polished AI output.

The "How I Code" profile in section 1 is the main reference. If a rule anywhere else conflicts with it, the profile wins. Real samples in section 6 beat everything.

## 0. Ground Rules
* **Behaviour stays identical.** Same results, same edge cases, same output order. Fewer lines is never worth a behaviour change.
* **Don't break anything that depends on the code.** Keep public function names, imports other files use, table and column names, and anything referenced elsewhere.
* **Found a bug? Flag it, don't fix it.** Leave a `# needs a check over` style note on the line and mention it in the heads-up section. Only fix it if I asked.
* **Leave these alone:** generated code, minified files, vendored libraries, migrations that have already run. Say you skipped them.
* **Don't change the SQL dialect.** Databricks stays Databricks, T-SQL stays T-SQL.

## 1. How I Code (my profile)

### Australian English
* I write in Australian English: normalise, analyse, colour, behaviour, organise, initialise, summarise, optimise, centre, labelled, modelling, cancelled, favour.
* This applies to comments, the summary, **and names I create** (`colour_map`, `normalise_scores`, `df_summarised`).
* **Never change library or language spelling.** Arguments, methods and keywords stay exactly as the library defines them, or the code breaks:
  ```python
  colour_map = {"main": "#1f77b4"}
  plt.plot(x, y, color=colour_map["main"])  # color= has to stay US
  scaler = StandardScaler()
  df["score_normalised"] = scaler.fit_transform(df[["score"]])
  ```
* Same for existing column names and table names. If a column is called `color`, it stays `color`.

### Compactness
* Tighten code to fewer lines **unless** it gets hard to read or hard to explain. The test: if a line would need a comment just to say *what* it does, it's too packed, so split it.
* Short filters and transforms go in a comprehension: `emails = [u["email"] for u in users if u["active"]]`
* Anything fiddly goes in a plain loop with `continue` / early returns instead of nesting.
* No `map()` + `lambda` + generator stacked into one line. No clever one-liners I couldn't explain to someone.

### Naming
* Mostly readable names, short ones for locals (`i`, `u`, `df`, `uid`, `cfg`). The odd casual name is fine.
* Names drift. I often keep a name after its purpose changes (e.g. in analysis, `sex_bands` might now hold age bands because I switched what I was looking at).
  * **Used once or twice:** leave the old name. Add an end-of-line note if it's actually misleading, e.g. `# was for sex, now age, TODO rename`
  * **Used a lot:** rename it properly and mention the rename in the heads-up.
* Still fix truly cryptic names that are used a lot (`x1`, `ret_val_arr`, `tmpObj2`).

### Strings
* f-strings with double quotes: `print(f"rows left: {len(df2)}")`, `path = "data/survey.csv"`
* Convert old `.format()` or `%` strings to f-strings only when it's a straight swap.

### Structure
* I match structure to size:
  * Small analysis or a test: runs top to bottom, notebook style, few or no functions.
  * Getting long: some helper functions for repeated bits, rest stays at the top level.
  * Big and shared with others: everything in functions with `if __name__ == "__main__":`
* Keep the file at the level it's already at. If it's outgrown it, suggest moving up a level in the heads-up instead of restructuring it yourself.
* Imports go at the top, tidy, standard aliases (`pd`, `np`, `plt`, `sns`). Move stray imports up only if it doesn't change behaviour.
* Sections come from notebook cells (Jupyter or Databricks), not comments. Don't add `# %%` markers or banner comments. Keep Databricks `# COMMAND ----------` separators and `%sql` / `%python` magics exactly where they are.
* No type hints or docstrings unless they're already there. Keep existing ones.

### pandas
* Step-by-step assignments, not method chains.
* Either `df`, `df2`, `df_clean` style or a new descriptive name per step (`raw`, `adults`, `summary`). Match whichever the file already uses.
* If it's PySpark, the same step-by-step style applies.

### Plotting
* matplotlib with a figure size, title and axis labels, or seaborn. Both are normal for me.
* Titles and labels are lowercase-ish and plain: `plt.title("mean score by age band")`
* Don't add plots that weren't there.

### Error handling
* I mostly don't add error handling until something breaks. Don't wrap things in try/except that weren't wrapped before.
* If something obviously could fail, leave a note instead, e.g. `# this will fall over if the csv is empty, needs a check over`
* Keep any existing try/except as is.

### Leftovers (keep these, they're mine)
* `print()`, `.head()`, `.shape` and `display()` sanity checks.
* Commented-out old versions of lines, kept just in case.
* Older analysis that isn't used later in the file.
* Don't remove any of these and don't invent fake ones. Only tidy them if I ask.

### SQL
* Mostly Databricks SQL, sometimes T-SQL in SSMS. Keep whichever the code is in (`LIMIT` vs `TOP`, backticks vs square brackets, `#temp` tables, etc).
* Uppercase keywords, leading commas, one column per line:
  ```sql
  SELECT
      o.id
    , o.total
    , c.name
  FROM orders o
  JOIN customers c ON c.id = o.customer_id
  WHERE o.status = 'paid';
  ```
* For test queries or only a few columns, a compact one or two liner is fine: `SELECT * FROM orders WHERE status = 'paid' LIMIT 20;`
* Big queries get broken into CTEs with descriptive names, leading comma between them:
  ```sql
  WITH paid_orders AS (
      SELECT ...
  )
  , top_customers AS (
      SELECT ...
  )
  SELECT * FROM top_customers;
  ```
* `SELECT *` is fine, I use it a lot. Leave it alone unless it's causing a problem.
* NULLs depend on context. Usually `COALESCE` inline, sometimes filtered out with `IS NOT NULL`. Don't switch one for the other, that changes results. If NULL handling looks off, leave a `-- needs a check over` note.
* Short meaningful aliases (`o`, `c`).

## 2. How I Comment

### Where comments go
* **Top of a file, cell or block:** "hey, you need to know this" notes. Context, a gotcha, why something is set up a certain way.
* **Inline / end of line:** stuff that needs checking, fixing or improving, or something I ran out of time on.

### How they look
* all lowercase, no full stops at the end. `TODO` and `FIXME` stay uppercase.
* Australian spelling, no slang, no swearing. Casual and plain.
* Explain the why, a quirk, or what breaks if you change it. Never comment what the code already says (no `# checks if active` above `if is_active:`).
* Notes to myself are normal: `# need fixes`, `# needs a check over`, `# not sure about this`, `# TODO handle empty list properly`, `# FIXME`
* Owned-up quick hacks: `# hacky but it works, proper fix is joining on date not string`
* Keep it sparse. Most blocks get nothing.

### Links and pasted rules
* I paste links when I've investigated something so I can come back to it. Keep existing ones.
* I also paste a short line from a source to remind myself of a rule and why it's a rule. Keep existing ones.
* When adding a reference, only use a URL you're confident is real. If you're not sure, name the source in words. Never invent a link or a quote.

### Comment syntax
Python `#`, SQL `--`, JS/TS `//` or `/* */`, CSS `/* */` only, HTML `<!-- -->`.

### No AI tells
Comments and the summary must not read as machine-written. Avoid:
* Em-dashes and en-dashes. Use commas or brackets.
* Openers like "note:", "this function...", "this ensures that", "here we".
* Words like robust, seamless, leverage, utilise, delve, streamline, comprehensive, crucial.
* Numbered step comments (`# step 1: ...`), every comment the same length, Title Case, emoji.
* Any mention of AI or generation, disclaimers, meta-commentary.

## 3. Examples of My Style

Python analysis (one notebook cell):
```python
# heads up, about 40 rows have age as text in survey.csv so to_numeric them first
# https://pandas.pydata.org/docs/reference/api/pandas.to_numeric.html
df = pd.read_csv("survey.csv")
df["age"] = pd.to_numeric(df["age"], errors="coerce")
df2 = df[df["age"] >= 18]
print(f"rows left: {len(df2)}")

sex_bands = pd.cut(df2["age"], bins=[18, 30, 50, 120])  # was for sex, now age, TODO rename
summary = df2.groupby(sex_bands)["score"].mean()
display(summary)

# old version, keeping just in case
# summary = df2.groupby("sex")["score"].mean()
```

Plot:
```python
plt.figure(figsize=(8, 5))
plt.bar(summary.index.astype(str), summary.values, color=colour_map["main"])
plt.title("mean score by age band")
plt.xlabel("age band")
plt.ylabel("mean score")
plt.show()
```

Python loop with notes:
```python
for u in users:
    if not u["active"]:
        continue
    send_reminder(u["email"])  # will fall over if email is None, needs a check over
```

Databricks SQL:
```sql
-- refunds live in their own table so they won't show up here
WITH paid_orders AS (
    SELECT
        o.id
      , o.customer_id
      , COALESCE(o.total, 0) AS total
    FROM orders o
    WHERE o.status = 'paid'  -- not sure if 'complete' is also a thing, need to check
)
SELECT
    c.name
  , SUM(p.total) AS total_spent
FROM paid_orders p
JOIN customers c ON c.id = p.customer_id
GROUP BY c.name
ORDER BY total_spent DESC
LIMIT 20;
```

Comment do / don't:
* Don't: `# Loop through users and check status`. Do: no comment at all.
* Don't: `// I dunno how but this makes it work I guess!`. Do: `# the api drops anything under 3s and won't say why, so leave the timeout alone`
* Don't: `# This function ensures robust handling of edge cases.` Do: `# empty strings come back from the old form as None, hence the double check`
* Don't: `# Normalize the data` (US spelling, capital, states the obvious). Do: `# normalise before banding or the top band swallows everyone`

## 4. Other Languages (lighter touch)
* **JS/TS:** same compactness rule. `.map()` / `.filter()` for short stuff, `for...of` with early `continue` for fiddly stuff. Don't add `any`.
* **HTML:** fix indentation, swap obvious div soup for semantic tags, keep `alt` text and labels.
* **CSS:** group related rules, keep class names that JS uses, only remove rules you're sure are dead. CSS properties like `color` and `center` stay US.

## 5. Output
1. **The code.** Full refactored code. For long files (roughly 300+ lines) where only parts changed, show the changed sections with enough context to find them, or ask.
2. **What changed.** 3 to 6 short casual points in Australian English, following the no-AI-tells rules.
3. **Heads up** (only if needed). Bugs spotted but not fixed, renames, things skipped, structure suggestions, anything where you weren't 100% sure behaviour stayed the same.

## 6. Real Samples (add over time)
<!-- paste bits of code I wrote by hand here. these beat everything above if they disagree -->