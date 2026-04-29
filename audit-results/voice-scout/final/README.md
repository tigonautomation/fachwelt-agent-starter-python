# Voice-Scout Phase 3 — Final A/B Render

**Date:** 2026-04-29
**Render model:** eleven_multilingual_v2
**Test script:** "Guten Tag, hier ist Lisa vom Fachwelt Verlag. Kurzer Hinweis: ich bin eine KI, das Gespraech wird zur Qualitaetssicherung aufgezeichnet. Wir bauen einen Marktplatz fuer B2B-Hersteller — haetten Sie zwei Minuten?"

## Settings

- **Setting A** — current production: stability=0.45, speed=1.0
- **Setting B** — slower deliberate: stability=0.55, speed=0.95

## Files (random listening order recommended)

| # | File | Voice | Setting | Levenshtein% |
|---|---|---|---|---|
| 1 | 01_WHaUUVTDq47Yqc9aDbkH_enniah_friendly_and_motivating_A.mp3 | Enniah - Friendly and Motivating | A | 94.6% |
| 2 | 02_WHaUUVTDq47Yqc9aDbkH_enniah_friendly_and_motivating_B.mp3 | Enniah - Friendly and Motivating | B | 95.1% |
| 3 | 03_NE7AIW5DoJ7lUosXV2KR_ela_cheerful_and_happy_A.mp3 | Ela - Cheerful and Happy | A | 95.6% |
| 4 | 04_NE7AIW5DoJ7lUosXV2KR_ela_cheerful_and_happy_B.mp3 | Ela - Cheerful and Happy | B | 95.6% |
| 5 | 05_pMrwpTuGOma7Nubxs5jo_lea_warm_and_supportive_A.mp3 | Lea - Warm and Supportive | A | 94.1% |
| 6 | 06_pMrwpTuGOma7Nubxs5jo_lea_warm_and_supportive_B.mp3 | Lea - Warm and Supportive | B | 95.1% |
| 7 | 07_rAmra0SCIYOxYmRNDSm3_lana_weiss_soft_and_sweet_A.mp3 | Lana Weiss - Soft and Sweet | A | 95.6% |
| 8 | 08_rAmra0SCIYOxYmRNDSm3_lana_weiss_soft_and_sweet_B.mp3 | Lana Weiss - Soft and Sweet | B | 95.6% |
| 9 | 09_v3V1d2rk6528UrLKRuy8_susi_effortless_and_confident_A.mp3 | Susi - Effortless and Confident | A | 95.6% |
| 10 | 10_v3V1d2rk6528UrLKRuy8_susi_effortless_and_confident_B.mp3 | Susi - Effortless and Confident | B | 95.6% |

## Pronunciation Statistics

- **Levenshtein% range:** 94.1% – 95.6%
- **Average:** 95.3%
- **Highest:** Ela - Cheerful and Happy (Setting A) at 95.6%

## Objective Recommendation

Highest Levenshtein%: **Ela - Cheerful and Happy + Setting A** at 95.6%.

(But pronunciation gap between top entries is small — final pick should be by ear.)

## How to Listen (Edon)

1. Open all 10 MP3s in a media player
2. Listen in random order — don't compare A vs B back-to-back per voice (bias)
3. Score each on: warmth, clarity, conversational feel, naturalness
4. Pick ONE voice_id + ONE setting

## Hard Reminders

- **Setting B** = slower (speed=0.95). Sounds more 'thinking out loud', fewer rushed words.
- **Setting A** = production default. More 'professional broadcast' feel.

## Top 5 voice_ids Reference

| Rank | Name | voice_id |
|---|---|---|
| 1 | Enniah | WHaUUVTDq47Yqc9aDbkH |
| 2 | Ela ⚠️ | NE7AIW5DoJ7lUosXV2KR |
| 3 | Lea | pMrwpTuGOma7Nubxs5jo |
| 4 | Lana Weiss | rAmra0SCIYOxYmRNDSm3 |
| 5 | Susi | v3V1d2rk6528UrLKRuy8 |

⚠️ = Phase 2 latency 511ms (+11ms over 500ms gate). Borderline, not disqualifying for B2B outbound.