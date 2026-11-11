# Video script — Outreach Desk (PS-3), one take, ~5 minutes

Full word-for-word narration. **Bold lines in brackets** are actions; everything in quotes is what you say.
One continuous screen recording, your voice, no slides, no cuts. Runs are live, so a few seconds of each run is
waiting time: the lines marked *(while it runs)* are written to fill exactly that.

---

## Before you press record

1. Open https://outreach-desk-lmgu.onrender.com about 2 minutes early (the free server takes ~50 s to wake).
2. Start the recording in a **private / incognito window** so the login screen appears on camera.
3. Close other tabs, mute notifications, browser zoom 100%.
4. No practice runs of Nithin Kamath or Sarah Riley in the hour before (free AI quota; 15 runs per hour per network).
5. Keep this script on a second screen or your phone.

---

## Scene 1 — Introduction (0:00 – 0:35)

**[Screen: the login screen.]**

"Hi, I'm Jatin Jain. This is my submission for Zamp's AI Solutions Analyst case study — problem statement three:
personalised outreach, from prospect to draft.

Here's the problem. A sales rep who wants to write a personal email has two bad options: spend twenty minutes
researching every prospect, or send something generic that gets ignored.

Outreach Desk takes a name and a company, researches public sources, and gives the rep a short draft built on one
real, sourced fact about that person. It never makes up facts, it flags anything risky, and a person always has the
final say — the tool cannot send email at all.

The demo is protected by an access code, which is shown right here for reviewers."

**[Click "Use this code". The New run page opens.]**

"The seller in this demo is Ledgerline — a fictional accounts-payable automation company. Its profile is
editable, and you can keep several seller profiles; that's the switcher in the top bar."

---

## Scene 2 — Happy path (0:35 – 2:35)

**[Scroll down to "Sample scenarios". Point at the card "Happy path — Zerodha's founder-CEO".]**

"Every sample here is a real, public business figure, chosen to trigger one specific route. This first one is the
happy path: Nithin Kamath, founder and CEO of Zerodha. It should go straight to a draft. I'll run it live — real
web searches and real AI calls, nothing pre-recorded."

**[Click "Run live". The run page opens and the stages start appearing.]**

*(while it runs — say each part as that stage lights up)*

"In the header you can see the time budget. Every run has a hard limit of ninety seconds, and there's a Stop
button if I want to end it early. If the limit is ever reached, it keeps everything it found and clearly marks the
result as incomplete.

The first stage is **identity** — making sure we have the right person. It searches the name with the company, the
role, and the company on its own. The AI reads the results and lists who these sources could be about, but every
piece of evidence it gives has to be a direct quote — and then code, not the AI, checks that each quote really
appears on that page. If the AI invents a quote, it's simply rejected. Here there's one clear match, so it carries
on.

Next, **gather**. It searches for interviews, talks and quotes by the person, plus company news from the last
twelve months — funding, hiring, launches, partnerships. LinkedIn and people-data brokers are excluded at the
search itself; I deliberately don't scrape LinkedIn, and only professional, public information is used.

Then **filter**. Code removes duplicates and stale items. The AI labels what each item is about, and anything
about a different person, anything personal, shopping pages, or anything that looks like hidden instructions aimed
at an AI — that's prompt-injection defence — is dropped or quarantined.

Then **rank**. The AI scores each item on how specific it is and how relevant it is to what Ledgerline sells, and
quotes the key fact. Code then adds recency and source credibility and computes the final score with a fixed,
weighted formula — the AI judges, but the arithmetic is in code.

And **route**. Plain rules, written in code, decide what happens next — not the AI. Here it's rule R7: a strong,
specific, safe signal about this person was found. So it writes a draft."

**[The draft appears in the "Draft" tab.]**

"Here's the draft. It's under a hundred and twenty words, with one hook, one problem Ledgerline solves, and one
low-friction ask. No flattery, no 'hope this finds you well'."

**[Point at the first line of the email and the hook title above it.]**

"The hook is this article — about how Zerodha grows its leaders from within instead of hiring senior people from
outside. Before any item is allowed to be the hook, the fact it relies on has to be found word for word in the
saved source text. So this opening line is grounded in something real — the AI didn't invent it.

After writing, there's a **genericness check**. The names are hidden and the AI is asked: would this exact email
still make sense if it were sent to two completely unrelated prospects? Here the answer is no — it's specific to
this person. If it were generic, the system would rewrite it once, and if it was still generic, flag it for the rep."

**[Click the "Signals & ranking" tab.]**

"This is the reasoning behind the choice. Every signal it found, the score breakdown for each one, why this one
won — and why every other one lost, including the items that were filtered out and the reason."

**[Click the "Identity" tab.]**

"And this is 'What we could verify': person, employer, role and company, each marked verified or not, with the
evidence behind it."

**[Click the "Draft" tab, then click "Approve".]**

"I'm the sales rep here, so the final decision is mine. I'll approve. Approving only marks the draft 'ready to
copy' — nothing is ever sent. I could also edit it, reject it, or ask it to regenerate with an instruction."

---

## Scene 3 — Edge case: outdated role + sensitive news (2:35 – 4:20)

**[Click "New run" in the top bar. Scroll to the card "Edge case — outdated role".]**

"Now an edge case — the kind that goes wrong in real life. I've got Sarah Riley down as the CFO of dbt Labs. That
sort of out-of-date detail sits in every CRM. Writing to someone about a job they no longer have is embarrassing,
and it tells them the email was automated."

**[Click "Run live".]**

*(while it runs — about 10 to 20 seconds)*

"It runs the same identity check. Watch what happens when the evidence doesn't agree with what I typed."

**[The run pauses and a decision panel appears.]**

"It stops and asks me. Public sources say Sarah Riley has moved on from dbt Labs and is now at Canva. Notice what it
does *not* do: it never quietly overwrites what I entered. It shows my details next to what the sources say, with
the evidence quotes, and leaves the decision to the rep. And the time spent waiting for me doesn't count towards
the ninety seconds."

**[Point at the evidence quotes, then click "Continue with the new details (Canva)".]**

"I'll continue with her current role."

*(while it runs — about 30 seconds)*

"Now the research follows her current company instead: the same gather, filter and rank stages, and then the
routing rules."

**[The draft appears.]**

"There are two things to notice here. First, the draft is about her new role at Canva — not the out-of-date one I
typed.

Second, the route is 'draft with warning', under rule R3. Canva announced layoffs about a week ago, and the system
recognised that as a sensitive signal. It will never use something like layoffs as a cheerful hook. Instead it
picks a safe hook, adjusts the tone — and look: the Approve button is disabled."

**[Point at the warning and the disabled "Approve" button. Tick "I've read the sensitive context and confirm the
tone of this message is appropriate." Approve becomes active.]**

"Only once I confirm that I've read the sensitive context can I approve it. That's the principle all the way
through: the AI reads and suggests, code decides, and the rep has the final say."

---

## Scene 4 — A second edge case in ten seconds (4:20 – 4:40) *(optional — skip if you're past 4:20)*

**[Click "New run". On the card "Edge case — fictional character", click "Run live".]**

"One more, very quickly: Tony Stark, CEO of Stark Industries."

**[It finishes in about 10 seconds.]**

"It recognises a fictional character, shows what's fiction and what's real — there are actually real companies
called Stark Industries — and refuses to write a draft. It will never invent a person."

---

## Scene 5 — Close (4:40 – 5:00)

**[Click "Dashboard" in the top bar.]**

"Finally, the dashboard. It tracks every run — the route it took, the outcome, how long it took, approval and edit
rates, and how often each edge case comes up. Failures show as failures; nothing pretends to have worked.

To sum up: Outreach Desk does the research, finds one real reason to write, flags anything risky or uncertain, and
always leaves the final decision to the rep. Thanks for watching."

**[Stop recording.]**

---

## If something goes differently on camera

It's live data, so small differences are normal. Keep going and say what you see:

| What happens | What to say |
|---|---|
| The hook is a different article | Read that hook's title instead of the Zerodha sentence in Scene 2; the rest still holds. |
| The happy path shows "Draft with warning" | "The rules flagged something — here's the reason it gives." Read the warning out. |
| A stage fails (red) | "The stage shows exactly what failed — it never pretends." Go back to New run and click **Replay** on the same card: it re-uses saved sources and is clearly labelled. |
| The 90-second limit is reached | "It hit the ninety-second limit — it keeps everything it found and marks the result incomplete. I can resume or run it again." Click **Run again**. |
| "Rate limit" message | You've used 15 runs this hour from your network: record later, or from another network. |
| The login screen doesn't appear | You're already logged in; use a private window, or skip straight to Scene 2's intro line. |
