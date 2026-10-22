Riggy dictated an idea for a small web project into his phone. Turn it into a
brief and return it as the structured output. Don't use any other tools or ask
questions.

- name: the product name. **If he named it ("called Leaf Peeper", "name it
  Canopy"), use exactly that.** Otherwise make up a short, friendly one, 1 to 4
  words. Never use the first words of the dictation as a name.
- slug: the subdomain it will live at: lowercase letters, digits and hyphens,
  2 to 31 characters, usually the name ("leaf-peeper").
- summary: one to three sentences on what it is and who it's for, in plain words.
  Leave out instructions about how to build it (mode, model, effort).
  Fill obvious gaps sensibly; don't invent big features he didn't mention.
- mode: `reckless`, `standard` or `rigorous` only if he asked for one (also
  "quick and dirty" means reckless, "be thorough" or "take your time" means
  rigorous). Otherwise `unspecified`.
- model: `fable`, `opus`, `sonnet` or `haiku` only if he named one ("use opus").
  Otherwise `unspecified`.
- effort: `low`, `medium`, `high`, `xhigh` or `max` only if he said one
  ("max effort", "extra high effort" means xhigh). Otherwise `unspecified`.

Dictation (speech-to-text, so expect typos and missing punctuation):

$dictation
