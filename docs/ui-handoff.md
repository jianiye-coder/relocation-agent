# UI Design Handoff: AI Moving Assistant (working name TBD)

Date: 2026-09-22 · Demo: Sunday 2026-09-27, shown to real users
Full PRD: [PRD.md](PRD.md)

## 1. What we're designing

A mobile-first web app that helps people moving within the US do three things, all driven by one photo inventory of what they own:

1. **Sell list**: photograph your rooms, see every item with its resale value, and get advice to sell, move, store or toss it. For items to sell, get a ready-to-post listing.
2. **Contact movers and storage**: the inventory becomes a quote request (items, volume, dates, addresses). The user reviews it and sends it to 3–5 movers from their own Gmail with one tap.
3. **Housing**: rentals that fit the user's budget, move-in date, commute and furniture.

The feeling we want: "This app did the tedious parts of my move for me, and I trust its numbers."

## 2. Who uses it

Anyone moving within the US: across town or to another state. Common cases:

- **New grad**: leaving student housing, tight budget, a few cheap pieces of furniture, needs a first apartment near work.
- **Job relocator**: a full apartment, a start date, maybe an employer relocation budget.
- **Local mover**: same city, lease ending, short deadline.
- **Couple combining homes**: duplicate furniture, bigger volume.

They're using a phone, often standing in a messy room, stressed and short on time.

## 3. Design constraints

- **Phone first**: design at 375 px wide. Photos are taken on the phone. It should also work on a laptop for the demo, as a centered column or a two-column layout.
- **Real users will try it on Sunday**, so every screen needs its loading, empty and error states. Don't design only the happy path.
- **Numbers must be explainable.** Any price or cost can be tapped to show how it was calculated.
- **Nothing is sent without the user seeing it first.** Sending must feel safe and deliberate.
- US only. Dollars, square feet, cubic feet, miles.
- Accessible: 4.5:1 text contrast, tap targets at least 44 px, and never color alone to show status.
- No brand yet. Propose a direction (see section 8).

## 4. Information architecture

```
First run:   Setup  ->  Room scan  ->  Stuff (inventory)
Then tabs:   [ Stuff ]   [ Movers ]   [ Housing ]
Overlays:    Item detail / Listing sheet, Gmail connect, Review & send, Send result
```

Bottom tab bar with 3 tabs. The user can add rooms later from Stuff.

**Not in scope for Sunday (don't design):** home dashboard, shopping list for the new home, address-change checklist, video scanning, login and account screens.

## 5. Screens

### 5.1 Setup (one time)
Purpose: collect the move details in under 30 seconds.

Fields:
- Moving from: city + ZIP (required)
- Moving to: city + ZIP (required); full new address (optional, "I don't have one yet" is a normal answer)
- Move date (required)
- Work or school address (optional; used for commute)
- Rent range, min and max (optional; needed for Housing)
- Current home: floor number, elevator yes/no (needed for mover quotes)

Primary action: "Next: scan my rooms".
Notes: keep it one screen if possible. Explain in small text why each optional field helps.

### 5.2 Room scan
Purpose: capture belongings room by room with 3–5 photos per room.

Content:
- Room picker (Bedroom, Living room, Kitchen, + Add room); progress "2 of 4 rooms"
- Camera / upload area with a tip: "Stand in a corner, get the whole room in each photo"
- Thumbnails of photos taken, removable
- After upload: items appear as they're found ("Found: bed, desk, chair, lamp...")
- Actions: "Add another room", "Done"

States: uploading; analyzing (takes 10–20 s per room, so it needs a good in-progress design); failed photo (blurry or too dark, retake); no items found.

### 5.3 Stuff (inventory + sell list)
Purpose: the main screen. See everything, decide what happens to each item, see what selling is worth.

Header summary:
- Items count · Expected from selling: $640 · Moving: 142 cu ft

Filter chips: All · Sell · Move · Store · Toss

Item row (repeat):
- Photo crop, name, condition (New / Good / Fair / Poor)
- Resale value and moving cost, side by side: "Worth $150 · Moving it: $180"
- Decision control: Sell / Move / Store / Toss, with the AI suggestion preselected and a one-line reason: "Moving it costs more than it's worth."
- Low-confidence items get a "Check this" badge ("Is this a desk or a table?")
- For Sell items: "Listing" button

Behavior: changing a decision updates the header totals immediately.

Item detail (sheet): edit name, category, condition; see how value and moving cost were calculated (tap to expand); delete the item.

Listing sheet: photo, title, description, suggested price; "Copy listing" and "Mark as sold (sold for $__)".

States: empty (no rooms scanned yet); still analyzing; all items decided.

### 5.4 Movers (quote requests)
Purpose: send one complete quote request to 3–5 movers or storage companies, from the user's own Gmail.

Content:
- **Your move summary**: what's being moved (142 cu ft, about 1,000 lb), from, to, date, floors/elevator. Editable.
- **Status banner** when there's no new address yet: "You haven't picked a new home. We'll send a preliminary request with your destination ZIP and send an update when you choose a home."
- **Movers list** (3–5 cards): company name, rating, "USDOT registered" badge, what they offer (full-service / container / truck / storage), checkbox to include (all checked by default).
- **Optional estimate row (P1)**: typical cost range by method (rental truck ~$1,850–2,350, container ~$2,300–2,900, full-service ~$4,600–6,200). Design it so it can be hidden.
- Primary action: "Review & send (4)".

Flow:
1. First time: **Connect Gmail** screen. Explain plainly: "We only ask for permission to send email. We can't read your inbox. Replies from movers go straight to you."
2. **Review & send**: swipeable or stacked previews of each email (to, subject, body), each editable; one "Send all" button.
3. **Result**: sent / failed per mover, "Replies will come to your Gmail." Later, when a home is chosen: "Send update to the same 4 movers" prompt.
4. Movers with only a web form: "Copy request" + "Open their form".

States: Gmail not connected; sending; partial failure (1 of 4 failed, retry); already sent (show sent date, and preliminary vs. updated).

### 5.5 Housing
Purpose: shortlist rentals that fit.

Content:
- Filters shown as chips: rent range, move-in by [date], max commute (minutes, transit / drive), beds
- "Your furniture needs at least 580 sq ft" note (calculated from kept items)
- List / Map toggle
- Home card: photo, title (1BR in Mission), rent, available from date, sq ft, commute "22 min by transit", a "Fits your furniture" or "Tight for your furniture" label, "Save", "View listing"
- On a saved home: "This is my new address" action. This updates the Movers tab and prompts "Send update to your movers?"

States: loading; no results (suggest relaxing a filter); listings source unavailable ("Showing results from 2 hours ago").

## 6. Key moments to get right

1. **The scan result reveal.** Photos go in, a priced list of the user's own stuff comes out. This is the "whoa" moment of the demo.
2. **"Sell this, it's not worth moving."** Worth vs. moving cost must read at a glance.
3. **Send all.** It should feel safe: full preview, clear recipients, sent from *your* Gmail.
4. **"This is my new address."** One tap connects Housing back to Movers (update request).

## 7. Sample data (use in mockups)

Move: Chicago, IL 60637 -> San Francisco, CA 94110 · June 15, 2026 · 3rd floor, no elevator · work: 1 Market St · rent $2,000–2,800

Items:

| Item | Condition | Worth | Moving it | Suggestion | Reason |
| --- | --- | --- | --- | --- | --- |
| IKEA 3-seat sofa | Good | $150 | $180 | Sell | Moving it costs more than it's worth |
| Queen bed frame + mattress | Good | $220 | $140 | Move | Costs $600+ to replace |
| Desk | Fair | $40 | $35 | Move | Cheap to move, useful at the new place |
| Office chair | Good | $60 | $20 | Move | Small and worth keeping |
| Bookshelf | Fair | $25 | $45 | Sell | Easy to replace |
| Floor lamp | Good | $15 | $8 | Toss | Not worth listing |
| Winter clothes (4 boxes) | — | — | $60 | Store | Not needed until November |

Movers: Bay Movers (4.7, USDOT registered, full-service), PODS (4.3, container), U-Pack (4.5, freight trailer), Chicago Storage Co. (4.6, storage units).

Homes: 1BR in Mission, $2,650, 610 sq ft, 22 min transit, fits · Studio in SoMa, $2,300, 450 sq ft, 12 min walk, tight fit · 1BR in Inner Sunset, $2,500, 640 sq ft, 35 min transit, fits.

## 8. Visual direction

Follow `DESIGN.md` in the project root (Airbnb-inspired): white canvas, near-black ink (#222222), one accent color (#ff385c) used only for primary actions, soft rounded shapes (8px buttons, 14px cards, pill toggles), one shadow tier, photography and prices carry the weight, not heavy type. Use Inter in place of Airbnb Cereal, and never use Airbnb's name, logo or Bélo mark.

## 9. What we need back

1. The Stuff screen in the DESIGN.md style first (the most important screen).
2. That style applied to all 5 screens plus the overlays in section 5.
3. Loading, empty and error states for Room scan, Movers and Housing.
4. A clickable prototype of the demo path: Setup -> Scan -> Stuff -> Listing -> Movers -> Connect Gmail -> Send all -> Housing -> "This is my new address" -> Send update.
5. Component list with tokens (color, type, spacing) so it can be built in Next.js by Friday.
