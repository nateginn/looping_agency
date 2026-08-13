# Google Business Profile structure — `art`

**As of 2026-08-12.** Operator-supplied, captured during the Track 1 GBP build-out.
Two profiles only: **Greeley** and **Denver**. The **UNC Campus** address is footer-only
and must never be modelled as a third profile.

This file is the durable record of what the profiles actually contain. It is documentation
— nothing in `tools/` reads it.

## Service-area terms used in descriptions

| Profile | Areas named (rotated across descriptions) |
|---|---|
| **Greeley** | Windsor, Evans, Milliken, Severance, Eaton |
| **Denver** | Jefferson Park, Union Station, Auraria, Sloan Lake, Villa Park, Highland |

## Credentials — exact wording, verified with the operator

- **Acupuncture is delivered by chiropractors holding acupuncture certification (CAc), not
  by a licensed acupuncturist (L.Ac.).** Descriptions must say
  `Provided by chiropractors certified in acupuncture.` Claiming L.Ac. would be inaccurate
  on a public medical profile. Applies to **both** locations.
- **Massage is delivered by licensed massage therapists (LMT).** `Licensed massage
  therapists (LMT)` is accurate.
- **Shockwave devices are FOCUSED (fESWT) at both clinics**, not radial. This is the
  clinic's genuine differentiator and should never be described as radial.

## Service constraints

| Service | Greeley | Denver |
|---|---|---|
| Focused shockwave / ESWT | ✅ | ✅ |
| Spinal decompression | ❌ not listed | ✅ Denver-exclusive |
| Acupuncture (CAc) | ✅ | ✅ |

---

## Greeley — categories and services (operator-confirmed 2026-08-12)

**Primary category: Chiropractor**
- Acupuncture
- Shockwave
- Chiropractic Care
- Auto Accident Chiropractor
- Back Pain Chiropractor
- Sciatica Chiropractor
- Herniated Disc Chiropractor
- Shoulder Pain Chiropractor
- ESWT Focused Shockwave

**Acupuncturist**
- Back Pain Acupuncture Treatment
- Neck Pain Acupuncture Treatment
- Sciatica Acupuncture Treatment
- Auto Accident Injury Acupuncture
- Work Comp Injury Acupuncture

**Physical Therapist**
- Physical Therapy
- Post-Surgical Rehabilitation
- Workers Compensation Injury Care
- Sports Injury Rehabilitation
- Auto Injury Physical Therapy
- Dry Needling

**Massage Therapist**
- Sports Massage
- Therapeutic Massage
- Injury Massage
- Rehab Massage Therapy
- Auto Injury Massage Therapy
- Neuromuscular Massage
- Trigger Point Massage
- Workman's Comp Massage Therapy
- Medical Massage
- Orthopedic Massage
- Massage Therapy *(the general catch-all)*
- Cupping Therapy *(unconfirmed — see open items)*

**Physical Therapy Clinic**
- Plantar Fasciitis Treatment
- Achilles Tendon Pain Treatment
- Calcific Tendonitis Treatment
- Tennis Elbow Treatment

**Sports Massage Therapist** — no services attached

## Denver — services built 2026-08-12

Descriptions were written for all of the below; the exact final set saved to the profile
was not re-confirmed field by field, so treat this as "descriptions supplied" rather than
"verified present".

Spinal Decompression Non-Surgical · Chiropractic Care · Auto Injury Chiropractic ·
Whiplash Treatment · Physical Therapy · Post-Surgical Rehabilitation · Workers
Compensation Injury Care · Sports Injury Rehabilitation · Auto Injury Physical Therapy ·
Dry Needling · Focused Shockwave Therapy · ESWT Focused · Back Pain Acupuncture Treatment ·
Neck Pain Acupuncture Treatment · Sciatica Acupuncture Treatment · Auto Accident Injury
Acupuncture · Work Comp Injury Acupuncture · Massage Therapy · Deep Tissue Massage ·
Sports Massage · Auto Injury Massage · Work Comp Injury Massage · Neuromuscular Massage ·
Medical Massage · Therapeutic Massage · Trigger Point Massage · Orthopedic Massage

---

## Open items

1. **A trailing "cup" appeared in the operator's list** after `Massage Therapy`. The
   general `Massage Therapy` catch-all is confirmed present; whether a separate
   **Cupping Therapy** service exists is unconfirmed. If cupping is offered, add it
   properly named; otherwise there is nothing to fix.
2. **Deep Tissue Massage is missing from Greeley.** `deep tissue massage greeley co` sits
   at position **14.3** in GSC — one of Greeley's better-ranking massage terms. Add it.
3. **`Sports Massage Therapist` is described as a second "primary" category.** A profile
   has exactly one primary; this is presumably an additional category. Worth confirming,
   and it has no services attached.
4. **Denver primary category not independently confirmed.** The artwebsite assessment
   verified `Chiropractor` as primary, but each location is set separately.
5. **Photos and reviews are the actual pack gap, and neither is addressed by services.**
   Greeley 55 reviews / 7 photos; Denver 7 reviews / 5 photos; pack incumbents at
   Cornerstone 225, Weld 434, REV 508, The Joint 656.
6. **Denver — still outstanding from the audit:** Thursday hours read "closed", and photos
   need to go 5 → 40+.

## Why this matters for the loop

Google names three local ranking factors — Relevance, Distance, Prominence. Everything in
this file is **Relevance**, which is now largely complete. **Prominence** (reviews, photos,
citations) is untouched by any of it and is where the measured gap actually is. Measuring
whether any of this worked requires the Maps connector (plan item 3b), since no Google
surface reports local pack position.
