# Write-up

**Result:** 1,598 dental businesses in Lahore from 504 Google Maps requests (about 25 minutes). The run was stopped
once and continued from where it left off. Full numbers: `benchmark/report.md`.

## 1. How I got the data, what else I tried, and why

**Google Maps.** A real browser (Playwright) opens Maps without logging in and does one normal search. While loading,
the page calls a hidden Google address that returns 20 businesses at a time. I copy that request and send it again from
inside the same page, changing only the search words, the map area and the page number. Each result already has the
permanent Google ID, name, category, address, location, phone, website, rating and usually the review count.
*Tried first:* reading the on-screen list (no website, one click per business) and scrolling while catching the data
(slower, breaks when the layout changes). Copying the page's own request on every run survives small Google changes.

- **Past the ~120 limit:** I cut Lahore's city border (from OpenStreetMap) into 5 km squares and search each one,
  stopping when a page brings nothing from inside the square. A square still full after 120 results is cut into 4
  smaller ones. Progress is saved after every page, so after a crash it continues at the same square and page.
- **Did I find everyone?** I searched "dentist" in 12 random squares and compared: "dental clinic" found 212,
  "dentist" 131, 122 were in both, only 9 were new. The capture–recapture formula (used to count wild animals)
  estimates 228 there, so we found **about 93%**, roughly 1,598 of ~1,700 in the city. It's an upper estimate,
  because both searches use the same Google ranking.
- **Duplicates:** the permanent Google ID means a business seen twice is stored once. I also merge different listings
  of the same clinic (same phone within 250 m, same website within 150 m, or same name within 60 m); chain branches
  are far apart, so they stay. 1,783 listings → 1,672 inside Lahore → 1,642 dental → 44 merged → **1,598**.
- **Websites:** home page plus up to 2 contact/about pages, respecting robots.txt; a real browser only when a page
  builds itself with JavaScript and no email was found. Hidden emails are found too (Cloudflare protection,
  "info [at] site [dot] pk", addresses built by JavaScript), and fake ones like `logo@2x.png` are dropped. To check
  that an email can receive mail **without sending anything**, I look up the domain's mail servers (MX).

**WhatsApp.** whatsmeow (allowed by the brief), through its Python version `neonize`: scan a QR once, and the login is
saved on disk, so it survives restarts and reboots. A small supervisor restarts the bot after a crash, waiting longer
each time, but stops and raises an alert if the number is logged out or banned. Voice notes are converted to text
**on the computer** (faster-whisper): no key, no cost, audio never leaves the machine. Replies are fixed messages;
`leads dental clinic Lahore` returns a summary plus the Part 1 CSV. *Alternative:* Baileys, but that's JavaScript.

**Replying exactly once.** Every message moves through saved steps: *received → reply ready → sending → sent*.
- A message is saved **before** WhatsApp is told we got it. If we crash first, WhatsApp sends it again, and a message
  we already have is skipped by its ID.
- The reply is saved first, and the message is marked "sending" just before it goes out.
- Hard case: a crash right after sending but before saving "sent". My library can't fix a reply's ID in advance, so
  after a restart the bot waits up to 60 seconds for WhatsApp's "delivered" tick. Tick arrives: it isn't resent.
  No tick: it's resent. Risk: a duplicate only if the other phone is offline that whole minute. A rare duplicate is
  better than a message that never gets an answer.
- The allowlist is checked when deciding to reply and again right before sending. My own messages, groups, old
  messages and unidentifiable senders are logged and skipped. A test switch (`WABOT_CRASH_AT`) kills the bot at each
  step to prove it.

## 2. What will likely break in 90 days, and how to notice within an hour

| What breaks | How we notice |
|---|---|
| Google changes its hidden data format | The program stops with a clear error, never "0 businesses". I'd add an hourly test search of one square against known businesses, and a check that the share with phone/rating/website hasn't dropped. |
| Google changes the Maps or cookie page | The first search can't start, so the hourly test fails. |
| Google slows us down / CAPTCHA | Detected; the run stops cleanly and the report says "blocked". Alert if it happens at all. |
| WhatsApp updates its protocol | The bot can't connect and the log shows a restart loop. Fix: update the library. |
| Number logged out (phone offline 14 days) | Immediate alert: CRITICAL message + line in `data/wa/ALERTS.log`; the bot stops. |
| Number banned, or opened on another computer | Same alert; the bot stops. |
| WhatsApp hides more senders' numbers | "Unknown sender" count rises in the message log; I'd alert on it hourly. |
| The bot freezes without crashing | I'd add a heartbeat to an outside monitor every 5 minutes. |

## 3. Cost: 10,000 businesses a day, and 100 times more

*Estimates from what I measured:* 504 requests found 1,783 listings (~3.5 new per request), each response is about
170 KB, 27% of businesses have a website, about 2.5 pages fetched per site.
- **10,000/day:** ~2,850 Maps requests (~480 MB, ~95 minutes) and ~7,000 web pages. One small server (~$20/month),
  plus residential internet traffic because Google slows down one IP used daily (~15 GB at $3–8/GB).
  **About $65–140 a month**, no paid APIs.
- **1,000,000/day:** ~285,000 requests a day and ~1.4 TB of traffic a month. That needs 100+ IP addresses
  (≈ $4–7k/month), Postgres instead of a database file, a job queue feeding many browser workers, re-checking only
  changed areas, and real monitoring. Compare with Google's Places API (~$1.5k a day at list price): about 10× more
  expensive, but legal and stable.

## 4. Terms of service and ban risk

Automated Maps searching breaks Google's rules, and unofficial WhatsApp tools break WhatsApp's. Risks: Google can block
the internet connection (no account to ban, since I never log in), and WhatsApp can ban the number for good.
**How I limited it:** Maps: one browser, one request every 2 seconds, at most 600 requests, stop at the first CAPTCHA
(never solve it, no proxies), robots.txt respected, public data only. WhatsApp: a separate test number, reply only to
allowlisted numbers, never start chats or message scraped businesses, at most 30 replies an hour, a reading pause and
typing indicator. **Use the official APIs** for anything with real customers or money: the WhatsApp Business API (no
ban risk), and the Places API when data is stored or sold, must be reliable, or legal safety matters more than cost.

## 5. What didn't work

- **My first square-splitting rule wasted requests:** it split any square with *any* results left after 120. In the
  first run 141 of 400 requests found nothing inside their square, and it hit the limit with 79 squares left. Now
  only busy squares split, a square stops after one empty page, and coverage is checked on a sample. I undid 15
  needless splits, and the continued run finished with 104 more requests.
- **Review counts are incomplete:** logged-out Maps hides them on screen, so I couldn't double-check, and 103 rated
  businesses have no count in the data; I left those empty instead of guessing.
- **More than 120 results from one search** worked once (up to 160); I didn't push it, to keep requests low.
- **Some WhatsApp numbers from website text were two numbers stuck together**; Pakistani numbers are now length-checked.
- **Voice:** speech-to-text hears "leads" as "Leeds" (the city), so the bot accepts both.
- A rare duplicate reply is still possible (section 1). The mailbox-exists email check is off by default: home
  internet blocks it, and Gmail/Outlook say "yes" to every address.
- 58 of 437 websites failed after 3 tries (mostly timeouts); 92 "websites" were Instagram/Facebook pages, saved as
  social links.
